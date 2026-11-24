"""Local image validation, transactional upload cleanup and guarded image access."""
from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import re
from uuid import uuid4
import warnings

from flask import abort, current_app, request, send_from_directory, session
from PIL import Image

from accounts import current_role
from storage import load_products

MAX_IMAGE_BYTES = 5 * 1024 * 1024
FORMATS = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}


@contextmanager
def new_image_files():
    """Remove newly created files if validation or the enclosing JSON save fails."""
    created = []
    try:
        yield created
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def decode_upload(upload):
    """Decode actual pixels and re-encode without trusting the client's filename or MIME."""
    content = upload.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError('每张图片最多5 MiB。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                format_name = image.format
                if format_name not in FORMATS or image.width * image.height > 20_000_000:
                    raise ValueError('只支持JPEG、PNG、WebP，图片不能超过2000万像素。')
                image.load()
                with (image.convert('RGB') if format_name == 'JPEG' else image.copy()) as clean:
                    with BytesIO() as output:
                        clean.save(output, format=format_name)
                        return uuid4().hex + '.' + FORMATS[format_name], output.getvalue()
    except (OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise ValueError('请上传有效的JPEG、PNG或WebP实物图片。') from error


def stage_product_images(product, *, partial, created):
    """Validate retained/new images and cover before writing files under the JSON lock."""
    previous = product.get('images', [])
    removed = set(request.form.getlist('remove_images'))
    if not removed <= set(previous):
        raise ValueError('只能移除当前商品已有的图片。')
    retained = [name for name in previous if name not in removed]
    uploads = [item for item in request.files.getlist('images') if item.filename]
    count = len(retained) + len(uploads)
    if count > 5 or (not partial and count < 1):
        raise ValueError('送审须有1–5张图片；草稿最多5张。')
    pending = [decode_upload(item) for item in uploads]
    names = retained + [name for name, _ in pending]
    position = request.form.get('cover_position', '').strip()
    cover = product.get('cover') if product.get('cover') in names else next(iter(names), '')
    if position:
        if not position.isascii() or not position.isdecimal() or not 1 <= int(position) <= count:
            raise ValueError('封面序号须对应保留或新上传的一张图片。')
        cover = names[int(position) - 1]
    write_images(pending, created)
    return {'images': names, 'cover': cover}


def write_images(pending, created):
    """Write verified bytes and track each new file for rollback on a failed JSON save."""
    if not pending:
        return
    directory = Path(current_app.config['UPLOAD_DIR'])
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in pending:
        path = directory / name
        with path.open('xb') as target:
            created.append(path)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())


def register_image_routes(application):
    """Keep uploads outside static and authorize every request using current product state."""
    @application.get('/products/<product_id>/images/<filename>')
    def product_image(product_id, filename):
        if not re.fullmatch(r'[0-9a-f]{32}\.(png|jpg|webp)', filename):
            abort(404)
        product = next((item for item in load_products(application.config['DATA_PATH'])
                        if item['id'] == product_id), None)
        if product is None or filename not in product['images']:
            abort(404)
        role = current_role()
        if product['status'] != 'approved' and not (
                role == 'admin' or (role == 'user' and product['seller'] == session['username'])):
            abort(404)
        response = send_from_directory(application.config['UPLOAD_DIR'], filename,
                                       conditional=False, etag=False, max_age=0)
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
