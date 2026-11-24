# 校园集：审核、查询与预约交易

最新验收记录见 [ACCEPTANCE.md](ACCEPTANCE.md)：70项测试，真实HTTP两条流程，通知后台重试；报告和最终ZIP待学生更新。

Flask + Jinja 普通表单，标准库 json 本地存储。复用实验二页面结构和 style.css，无 SQL、ORM 或独立前端工程。

## 运行

推荐在包含 `code/` 和 `UML/` 的提交根目录（当前为 lab3）安装，适用于解压后的目录：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r code/requirements.txt
.venv/bin/python -m pip check
.venv/bin/python code/app.py
```

本轮已在独立 Python 3.12.3 虚拟环境实际安装 requirements.txt（含 Pillow 12.0.0），pip check 无冲突。已有环境也须重新安装依赖。
在同一提交根目录执行测试和检查：

```bash
.venv/bin/python -m unittest discover -s code -v
PYLINTHOME=/tmp/se-lab3-pylint .venv/bin/python -m pylint code/*.py
```

当前机器也可以直接使用已有 Anaconda；以下快捷命令从 courses/SE/lab 工作区根目录执行：

```bash
/home/szmwtx27906/anaconda3/bin/python lab3/code/app.py
```

访问 http://127.0.0.1:5000 。其他机器可在虚拟环境安装 requirements.txt 后运行 app.py。

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 普通用户/卖家演示 | user | user123 |
| 普通用户/买家演示 | buyer | buyer123 |
| 普通用户/另一买家 | other | other123 |
| 管理员 | admin | admin123 |

普通账号均可买卖。使用不同浏览器或无痕窗口演示多账号。四个预置账号继续按上表登录；演示密码保持原样，新增注册账号适用下面的校验。

## 普通用户注册与登录

登录页提供“注册普通用户”入口，访问 `/register` 填写用户名、密码、昵称和联系方式；成功后跳转 `/login`，不会自动切换成新账号。
字段依据实验一 REQ-001 和实验二 campus-market-ui.html 的注册原型：四项必填，密码至少8位，管理员不能通过注册创建。
本地校验上限为用户名/昵称50字、联系方式200字、密码128位；用户名不能含空白，密码至少8位且不能全为空白。
用户名、昵称和联系方式去除首尾空白；密码不裁剪。用户名按大小写精确比较，保留 user、buyer、other、admin 四个演示名。
重复用户名返回409，非法字段返回400；登录失败统一返回“账号或密码错误”，不说明用户名是否存在。

账号写入与已有业务数据共用 data.json，在同一 transaction 内查重并保存，因此并发注册同名最多成功一次。
注册请求中的 role 被忽略，新增账号固定为普通用户；只有预置 admin 具有管理员身份。
使用 Werkzeug generate_password_hash/check_password_hash，JSON 仅存 password_hash，不存明文密码。
联系方式不在公开商品列表或详情展示。在“我的交易”中，仅对应买卖双方可在待卖家确认、已确认、已完成状态查看对方联系方式；取消、拒绝、超时后不再展示。直接联系方式请求也检查当前身份和状态，并禁止缓存。演示账号或旧数据缺少联系方式显示“未填写”。管理员仅可在本轮新增的有效终止申请处置页或符合状态要求的违规处置页查看双方联系方式，不开放通用接口。
旧 JSON 没有 users 字段时默认空列表，演示账号仍可登录；新账号直接从 JSON 读取，重启后可继续登录。

演示：退出当前账号 → 登录页点“注册普通用户” → 填写四项（例如 campus_student / student123）→ 注册后在登录页登录 → “我的商品”可用，“商品审核”返回403。昵称和联系方式请使用演示值。

## 功能与页面

- `/`：公开列表，关键词匹配标题/描述；分类、最低价和最高价取交集，金额边界包含等号；非法金额及反向区间返回400并提示修正。
- 排序支持价格升降序、发布时间升降序，默认最新在前；同值按商品编号升序固定次序；先筛选、再排序、最后按每页20条分页。翻页保留关键词、分类、价格区间及排序；重新搜索回到第一页。旧商品未知发布时间为空：最新排序时置后，最旧排序时置前。
- `/products/<id>`：公开详情，仅已发布商品可见，其他状态即使管理员访问也返回404。
- `/submit`、`/mine`、`/edit/<id>`：草稿允许缺字段但已填值须合法；完整送审。本人可撤回待审、编辑草稿；已发布商品修改后必须重新审核并立即停止公开。有有效预约时禁止编辑。
- `/off-shelf/<id>`：本人可普通下架无有效预约的已发布商品；下架后列表和公开详情均隐藏。下架商品可保存修改（保持下架），也可重新送审，审核通过才重新公开；原审核历史保留。
- `/register`、`/login`：普通用户注册及演示/持久账号登录。
- `/admin`：管理员通过或驳回；驳回回草稿并保存原因。审核结果和追加历史在同一次保存中提交。
- `/reviews`：管理员看全部审核历史；卖家只看本人商品。每条记录保存商品编号/标题快照、审核人、UTC时间、被审核版本、决定及原因，重审不覆盖历史。
- 列表与详情上的预约表单明确显示价格；服务器同时提交隐藏的页面版本、确认价格和新动作标识。
- `/booking-requests/<request_id>`：仅原买家可查询首次处理结果及关联预约的当前状态。
- `/reservations`：仅参与者可看自己的交易；买家预约→卖家确认→买家确认收货完成。
- 待卖家确认期间，买家可取消，卖家须填写原因拒绝；两者均同步恢复商品公开，保留旧预约。

## 本地演示步骤

1. 启动上面的 app.py 命令，以 user 登录，新建商品填写标题、描述、分类、价格、交付地点，上传1–5张JPEG/PNG/WebP实物图。草稿可不完整；送审须完整。可填封面序号（从1开始）；不填默认第一张。退出后用 admin 审核通过，公开列表显示封面，详情显示地点及全部图片。
2. user 进入“我的商品”，点击“普通下架”。确认状态为“已下架”，公开搜索查不到，原公开详情链接返回404。
3. 点击该商品的“编辑 / 重新送审”。可以“保存修改（保持下架）”；点“再次送审”后进入待审核，不能直接恢复发布。
4. admin 再次审核通过；公开列表和详情恢复，审核历史显示两次通过记录。其他普通用户和管理员不能代卖家普通下架。
5. buyer 预约后，卖家下架请求会被拒绝；待确认和已确认预约都属于有效预约。卖家确认、买家完成交易。要演示真实联系方式，请先注册两个使用虚构联系方式的账号分别买卖，在“我的交易”核对显示的对方信息；other 及匿名访问不得查看。
6. 准备至少21件审核通过的商品，访问公开列表，按价格或发布时间排序后使用“上一页 / 下一页”。添加关键词、分类和价格范围，检查翻页后表单条件保持不变。
7. 无需手工造数据时，可运行下方分页专项测试：它临时创建45件同值商品和1件隐藏商品，并逐页验证20/20/5条、ID顺序及筛选后的20/5条，不改变演示数据。

```bash
PYTHONPATH=lab3/code /home/szmwtx27906/anaconda3/bin/python -m unittest test_listing.PaginationTest -v
```

图片专项演示：先复制已发布图片链接；下架或修改重新送审后，在退出登录的窗口访问原链接应返回404，卖家本人和审核管理员仍能查看。在编辑页可移除旧图、上传新图，并按“保留的旧图→新上传图”的顺序指定封面；表单校验失败后须重新选择未保存的新文件。旧商品无图时展示占位图，缺地点显示“未填写”。

页码必须为正整数：非法格式、0或负数返回400；超出末页返回404。无结果第一页显示“暂无商品”，无翻页链接。
分页稳定保证针对同一数据集；并发上架、下架或改价后数据集变化，需要刷新重新查看。

## 旧页面与重复提交（REQ-017、018）

预约 POST 必须带 `request_id`、`expected_version`、`expected_price`；缺失/非法返回400，不按当前服务器值补齐。
在同一 transaction 锁内先检查已有请求：

1. 同一买家、同标识、规范化后相同内容，返回首次结果和关联预约当前状态；商品改价、预约取消/拒绝/超时/完成都不会改变该标识的原含义。
2. 同一买家、同标识、不同商品/版本/价格，返回409，不覆盖原处理。
3. 新标识且该买家已有同商品有效预约，记录并返回已有预约，不新增预约。
4. 否则核对归属、当前状态、有效预约及页面版本/价格；不一致时保存首次失败结果，返回明确业务原因。改价提示当前价格并要求刷新、重新确认，不能静默接受涨价。
5. 全部通过才同时保存预约/快照、商品占用和请求成功记录。写入失败整体不提交，可用原标识重试。

标识按买家隔离，不同买家使用相同标识互不冲突。业务失败结果也持久保存；重放失败不会因商品后来恢复可售而自动成功。
商品编辑及状态变化递增 version。预约快照记录的是买家确认的商品版本和价格。
预约和管理员通过/驳回均校验页面版本。审核表单提交 expected_version，锁内先确认仍为待审核，再比对当前版本；缺失/非法返回400，版本不一致返回409并提示刷新，不更新商品、不追加历史。
本轮已补齐既有对象修改操作的页面版本协议，完整清单见下文。新建商品、注册不存在既有对象版本；超时按锁内时间与状态校验。

审核缺陷演示：管理员打开“商品审核”后保留旧页 → 另一会话用卖家撤回、改价、重新送审 → 原管理员页面点通过或驳回，得到409 → 刷新后看到新版商品，重新审核可正常完成。旧审核 POST 示例也必须补上从页面取得的 expected_version，不能由服务端自动用当前版本代替。

## 时间、存储与兼容

新预约保存 UTC created_at/expires_at，严格以创建后24小时为界；截止时刻起不能确认或拒绝。
页面访问和直接业务请求触发超时检查，交易写入锁内再次检查；waiting→timed_out 时同次保存将商品恢复 approved。
已确认预约不会被确认期限超时。预约超时没有独立后台定时器，无访问时不自动落盘；下次访问/操作处理。通知投递另有后台线程，不负责预约到期处理。
测试通过 app.config['CLOCK'] 注入带时区时间，不实际等待。

默认文件是代码目录中的 data.json，与启动目录无关；MARKET_DATA_PATH 可覆盖（父目录须存在）。
顶层保留 products、reservations、reviews、booking_requests，并兼容新增 users 账号列表。
旧商品列表格式仍兼容，旧 rejected 作为 draft 读取，保留原因。
旧商品缺少地点、图片和封面分别补空字符串、空列表和空字符串；仍能读取、展示。旧商品再次送审时也须满足地点及1–5张图片要求。
旧商品缺少分类时默认为“其他”、缺版本时为1、缺发布时间时显示未知；保留已有值，下一次业务写入保存迁移后的完整文档。
不捏造旧审核历史：原 reason/reviewer 仍保留，无法恢复的历史时间不补造；新审核开始追加完整记录。
旧 waiting 缺少截止时间时，有创建时间则从该时间计算，否则从首次访问起算并持久保存24小时窗口。
旧预约没有请求标识，不伪造旧请求记录；同买家新请求仍可关联已有有效预约。

所有写入经过 storage.transaction：同一 RLock 包住读文件→校验→修改→原子保存。
临时文件与原文件同目录，json.dump、flush/fsync 后 os.replace；异常不提交，内容无变化不重复替换。
只支持单服务进程；不同进程不共享此锁。预置账号与默认密钥用于本地演示，SECRET_KEY 可覆盖。

图片默认存入 data.json 同级 uploads/，可用 MARKET_UPLOAD_DIR 覆盖；不可把它映射成公开 static 目录。JSON 只记录随机图片名称和封面名称，备份时需一起备份 JSON 和图片目录。只接受实际可解码的JPEG/PNG/WebP，每张最多5 MiB、2000万像素，请求总上限26 MiB；重新编码后保存，不信任客户端文件名或类型。图片链接每次检查当前商品状态和权限，并返回 no-store；已下载的副本无法远程撤回。
图片写入在 transaction 锁内完成；校验或JSON保存失败清理本次新增文件。移除图片只移除JSON引用，旧文件保留但路由拒绝访问，避免失败时破坏原数据。异常进程退出可能留下未引用文件；尚无自动清理任务，文件和JSON并非跨介质事务。
uploads/、运行用 data.json 均不入Git或提交包，干净副本无需预建它们。

## 阅读顺序

本轮优先阅读：app.py 的 validate_product / submit_product / edit_product；privacy.py 的 counterparty_contact / register_contact_routes；images.py 的 stage_product_images / decode_upload / product_image；storage.py 的 load_data / transaction。对应 submit.html、products.html、reservations.html、image_placeholder.html。

1. accounts.py：validate_registration、register、find_account、current_role、login；理解注册查重、密码哈希和普通用户角色。
2. storage.py：load_data、transaction、_save_data，理解兼容和保存边界。
3. catalog.py：parse_bound、query_products、paginate_products、register_catalog_routes；对应 products.html 的搜索表单和详情。
4. app.py：has_active_reservation、take_off_shelf、edit_product；随后阅读 review_product、review_history；对应 reviews.html。
5. booking.py：parse_booking_form、booking_decision、process_booking、associated_reservation，理解参数规范化、首次结果和重放。
6. app.py：reserve_product、booking_result、expire_reservations、transition_reservation；对应 products.html、booking_result.html、reservations.html。

## 验证（历史记录，当前结果见文末）

从工作区根目录执行：

```bash
/home/szmwtx27906/anaconda3/bin/python -m unittest discover -s lab3/code -v
PYLINTHOME=/tmp/se-lab3-pylint /home/szmwtx27906/anaconda3/bin/python -m pylint lab3/code/*.py
```

历史记录：原32项回归继续保留并补上审核表单版本；审核修复完成后33项通过，注册实现后36项通过。本轮地点完成后38项通过；联系方式相关4项通过；最终独立安装依赖的环境全量46项通过（37.157秒）。新增10项覆盖地点/图片旧数据、提交校验、联系方式身份和状态矩阵、图片上传/封面/访问权限及JSON保存失败清理。新增测试覆盖旧审核页面的完整操作链、注册登录、重复/保留用户名、并发注册、新账号权限及独立进程重启后登录。
最终 Pylint 退出码0，10.00/10。测试使用临时数据，不污染演示文件。
原预约测试补上正式表单参数；超时后的旧页面先得到版本失效，再刷新确认成功。
本轮还通过真实 HTTP 检查搜索、隐藏详情、历史权限、页面隐藏字段、预约重放及服务进程重启后的原结果读取。
本轮另以独立临时 JSON 和随机本地端口通过真实 HTTP 检查注册、登录、普通用户权限及旧/当前审核表单；服务和数据已清理。浏览器视觉和点击交互尚未验证。

本轮另用独立临时JSON、图片目录和随机端口进行真实HTTP请求，验证注册双方、上传与封面、地点、发布图片可访问、下架/重审旧图片链接404、卖家/管理员访问、预约双方联系方式、无关用户禁止访问及取消后禁止查看。未做浏览器点击或布局验收。

## 剩余范围

已实现普通下架与重新送审、稳定20条分页，并补齐交付地点、商品图片与封面和交易参与者联系方式。不能据此宣称实验一全部需求已实现。
本轮已补齐关键业务日志、通知投递与重试及既有对象写操作的版本协议。仍未实现或未专项验收的其他范围包括REQ-021统计、REQ-023/024性能、REQ-027真实用户易用性、REQ-031/032备份恢复及REQ-033浏览器适配。
实验说明未明确测试、HTML/CSS、空行和复用代码的计入规则，需确认；不以扩行代替需求实现。
已初始化 lab3 本地 Git 仓库（main）；本地提交及工作区状态以 git log、git status 为准，远程配置以 git remote -v 为准。实验报告由学生独立撰写。

## 历史行数（提交61cc731，未含后续终止、违规及日志通知流程）

实际执行 `wc -l code/*.py code/templates/*.html code/static/*.css`，并用 Python 的 `splitlines()` 和 `bool(line.strip())` 统计物理行与非空行（从 lab3 根目录执行）。

| 类别 | 物理行（含空行） | 非空行 |
| --- | ---: | ---: |
| Python 实现 | 889 | 770 |
| Python 测试 | 1342 | 1202 |
| HTML/Jinja | 139 | 139 |
| CSS | 7 | 7 |
| 合计 | 2377 | 2118 |

Python 实现：accounts.py 98、app.py 374、booking.py 94、catalog.py 92、images.py 110、privacy.py 49、storage.py 72行。
Python 测试：test_accounts.py 135、test_app.py 527、test_details.py 276、test_features.py 230、test_listing.py 174行。
style.css 1行直接复用实验二原型，forms.css 6行是手写扩展；HTML/Jinja沿用原型结构扩展，图片占位为手写SVG。
未发现 GUI 工具自动生成代码。统计不含README、报告、UML、运行数据和上传文件；测试及复用界面能否计入课程源码规模仍需确认，不将总行数直接认定为课程合规行数。


## 提交前验收与打包边界

本轮实际启动 code/app.py，MARKET_DATA_PATH 指向独立临时目录，通过真实 HTTP 验证：

- user 保存不完整草稿并送审，admin 通过，buyer 预约，user 确认，buyer 完成；JSON 中商品与预约最终均 completed。
- 另一件已发布商品下架前旧详情链接200，下架后404。
- 通过真实提交/审核创建26件筛选商品，下架其中1件后，带关键词、分类、最低/最高价和排序翻页为20/5条；全部条件保留，无重复遗漏。
- 四个演示账号均能登录，样式资源返回200。验收服务器已停止，临时数据已清理，code/data.json 未用于验收。

浏览器控制返回 `The browser access host is unavailable`，故未完成真实浏览器点击、布局、键盘和视觉验收，不能将上述 HTTP 检查称为浏览器验收。
初次提交前的验收记录：原环境及独立 Python 3.12 新安装环境均32项通过，Pylint均10.00/10；注册与审核修复后为36项，地点、联系方式及图片一轮为46项，当前最新回归见文末。

lab3/UML 已准备实验二原始设计快照（7份 puml + 7份 PNG），保留实现差异说明；不能把它当作全部功能均已实现的证明。
提交包须使用以下结构；实验报告由你自行核对，本轮不生成最终 ZIP：

```text
实验3_学号_姓名/
├── 实验报告.pdf        # 由学生独立完成
├── UML/
│   ├── 类图.puml
│   ├── 类图.png
│   └── ...
└── code/
    ├── app.py
    ├── requirements.txt
    ├── templates/
    ├── static/
    └── ...
```

.gitignore 已排除 .venv、缓存、code/data.json、code/uploads/、日志、ZIP 导出文件及课程说明 PDF；本地文件仍保留。
运行数据不需要打包：应用支持缺失 data.json，第一次业务写入自动创建。所有代码、模板、样式、依赖清单、测试和 UML 应保留。
.gitignore 不会自动过滤手工压缩。不要直接压缩整个 lab3，也不要将 .git、.venv、缓存和自己的真实运行数据放入提交包。

待报告和代码均由你检查并提交到本地 Git 后，可在 lab3 根目录导出干净包（替换学号姓名）：

```bash
git cat-file -e HEAD:实验报告.pdf
git archive --format=zip --prefix=实验3_学号_姓名/ -o /tmp/实验3_学号_姓名.zip HEAD
```

第一条确认报告已经进入提交；失败时先补齐报告再执行归档。归档只含已提交内容，需检查报告、code 和 UML 都已提交，且未强行纳入忽略文件。
已有origin远程配置；最终验收后按授权提交/推送并核对远程记录，当前哈希以git log及最终回复为准。课程行数口径待确认；本README保留分类统计，不将包含测试和界面的总物理行直接认定为合规达标。

## 已确认交易的终止申请与审批（REQ-019）

在“我的交易”中，已确认且未完成的交易买卖双方均可填写1–500字理由申请终止；同一预约同时只保留一条待处理申请，重复申请返回原交易页。管理员从“终止审批”列表进入处置页，领取该申请后可查看双方联系方式（旧账号缺失时为“未填写”），线下联系核实并填写双方意见及处理原因。只有明确勾选“已核实买家同意”和“已核实卖家同意”才可批准；这是管理员对核实结果的记录，并非系统自动取得双方同意。不同意时可驳回，交易保持已确认。

批准在同一个 transaction 中将预约转 terminated、商品转 off_shelf、申请转 approved，并追加处理事件和双方通知待发送记录。申请、处理记录和通知意图保留在 termination_requests、termination_events、notifications 中；旧JSON缺少这些字段时补空列表。通知现已按文末的站内投递流程发送；违规处置使用下述单独流程。

买家仍可确认完成；完成时在同一锁内关闭待处理申请。管理员旧审批随后返回409，不能再终止已完成交易。审批还提交并复核商品页面版本。处置页只有管理员可访问，只有申请待处理且交易仍已确认时显示联系方式，处理结束后旧处置链接返回409；队列、审核页、公开页不显示联系方式，管理员没有通用联系方式接口权限。处置页禁止缓存。

演示顺序：buyer预约 → user确认 → 任一方在“我的交易”申请终止 → admin进入“终止审批”，记录双方核实意见并批准或驳回。旧页场景：admin保留处置页 → buyer完成 → admin提交旧审批，应返回409。自动测试使用隔离JSON及Flask测试客户端，不写运行数据。

优先阅读 termination.py 的 request_termination、handle_termination、validate_decision、audit、close_on_completion，以及 app.py 的 transition_reservation。该轮未更新报告或提交；最新提交状态见最终验收记录。

本轮实际验证：在lab3目录执行 `/tmp/lab3-media-env-vFOw7j/bin/python -m unittest discover -s code -v`，52项通过（45.277秒）；执行 `PYLINTHOME=/tmp/lab3-termination-pylint /tmp/lab3-media-env-vFOw7j/bin/python -m pylint code/*.py`。初次检查发现状态标签一行过长，已换行修正后重跑。
最终Pylint为10.00/10，退出码0；`git diff --check`通过。


## 管理员违规处置与解除限制（REQ-008、015、020、030）

admin 登录 → “违规处置” → 选择商品，预览商品当前版本、状态及全部关联预约和交易价格快照 → 填写1–500字违规原因并提交。处置页隐藏字段包含商品版本与商品/关联预约的快照摘要；POST在 transaction 锁内重新读取并比较，再核对有效预约与商品状态是否一致。旧页面返回409，不追加处理或通知记录。

未完成商品转 violation（违规下架）；waiting 或 confirmed 预约同步 terminated，并关闭其待处理的普通终止申请。已完成商品保持 completed，预约及历史原样保留，仅设置 violation_active 限制标记并追加违规记录。商品列表、详情和图片公开链接不能绕过隐藏规则；卖家不能编辑违规商品或直接送审恢复。

解除限制须填写原因，未完成商品转 off_shelf，仍不公开；卖家修改、重新送审并获管理员审核通过后才能公开。已完成交易解除限制后依然保持完成状态，不可重新销售。已违规商品允许有新依据时再次处置并追加记录。

管理员仅在执行违规处置前、关联预约为 waiting/confirmed/completed 时能在专用处置页查看双方联系方式。取消、拒绝、超时、终止预约不显示；限制生效后的页面及解除页面不显示联系方式，队列和历史不显示联系方式，所有相关页面禁止缓存。管理员没有全局联系方式查询权限。

moderation_events 保留操作者、时间、原因、处置前后商品和预约快照；notifications 保存卖家及受影响交易买家的待发送通知（不保存联系方式），与预约终止、申请关闭、商品状态一起原子提交。发生保存失败则业务状态、处理历史和通知意图全部保持原样，旧JSON缺字段自动兼容。通知投递与重试现已实现，见文末。

重点阅读 moderation.py 的 handling_snapshot、check_links、apply_moderation、save_moderation_event、moderate_product；回归在 test_moderation.py。该轮未修改报告或提交；最新状态见最终验收记录。

本轮实际验证（lab3目录）：

```bash
/tmp/lab3-media-env-vFOw7j/bin/python -m unittest discover -s code -v
PYLINTHOME=/tmp/lab3-moderation-pylint /tmp/lab3-media-env-vFOw7j/bin/python -m pylint code/*.py
git diff --check
```

全量58项通过（28.738秒），Pylint修正行长、函数参数数目和条件复杂度后为10.00/10（退出码0），git diff --check通过。另通过独立Flask测试客户端逐项验证七种预约状态的违规处置联系方式权限：waiting/confirmed/completed允许，cancelled/rejected/timed_out/terminated隐藏。均使用临时数据；未做本轮浏览器点击和布局验收。


## 完整版本协议、统一日志与站内通知

### 第一组：REQ-018版本检查清单

| 修改操作及关联需求 | 本轮前 | 当前实现及表单 |
| --- | --- | --- |
| 编辑、草稿保存、重新送审（REQ-006） | 缺失 | app.edit_product → operations.check_version；submit.html |
| 撤回待审（REQ-004） | 缺失 | app.withdraw_product；products.html |
| 普通下架（REQ-007） | 缺失 | app.take_off_shelf；products.html |
| 管理员通过/驳回（REQ-005） | 已有 | app.review_product，保留审核版本与状态校验 |
| 新预约（REQ-011/017） | 已有 | booking.process_booking，保留原请求重放优先，再对新请求校验状态/版本/价格 |
| 买家取消、卖家确认/拒绝、买家完成（REQ-012/013/014） | 缺失 | app.transition_reservation；reservations.html |
| 终止申请（REQ-019） | 缺失 | termination.request_termination；reservations.html；已有待处理申请不新建 |
| 终止审批（REQ-019） | 已有 | termination.handle_termination，保留 |
| 违规处置及解除（REQ-008/015） | 已有 | moderation.moderate_product，保留版本+全部关联预约快照 |

新增版本字段均在 transaction 锁内比较商品当前版本（作为关联交易的共享版本）。缺失/非法400，失效409，页面提示刷新。没有服务端自动补版本。旧页面失败后商品、预约、审核及处置历史不变，仅新增失败操作日志。新建商品、注册不读取既有对象版本；系统超时按当前时间与状态检查。账号登录/退出不属于对象版本协议，通知已读是本人对象上的幂等标记。

测试夹具FreshFormClient只为既有回归模拟新页面提交版本；显式旧版本不被替换，缺字段测试使用原始open(method='POST')，生产路由不会补版本。

### 第二组：REQ-020/022统一操作日志

storage.transaction 在原子保存前调用 eventlog.journal_changes，对商品、预约、审核、终止申请/处理事件、违规处置记录的变化登记成功日志。复用专用终止/违规记录，不另行复制原因正文或联系方式。关键日志包含日志编号、服务端请求编号、操作者或system、UTC时间、操作类型、对象编号、前后状态、结果及受控原因码；详细业务理由按对象编号查原处理记录。失败操作在事务回滚后单独保存日志，不提交业务修改；同键预约重放不重复成功日志或通知。

admin → “操作日志”可组合筛选actor、object_id、operation、result、start、end。字符串按精确值匹配；时间ISO格式，未带时区按UTC，起始包含、结束不包含。result为success或HTTP错误码。没有日志写改删业务接口，不自动清理，保留时间超过180天且课程结束前不清理。

日志采用字段白名单，不复制表单、响应正文、异常详情、密码、密码哈希或联系方式。失败原因使用受控码（如rejected_409、storage_error），保留原业务状态。存储写盘短暂失败时，尝试单独提交失败日志；若存储持续不可用，失败日志无法保证落盘，会输出不含敏感内容的存储错误，原异常仍向调用者返回，不冒充业务成功。

### 第三组：REQ-013/019/020及UML Notification站内通知

notifications列表仍是持久待发送队列，新增inbox保存用户通知。预约创建、确认、取消、拒绝、超时、完成，以及审核结果登记通知；普通终止和违规处置继续使用已有通知记录。通知意图、成功日志与业务变更同一次JSON保存，保存失败则不生成可投递的新事件。

notifications.deliver_pending扫描pending/retry记录；deliver_one在独立transaction中按待发送记录ID与接收人检查去重，写入收件箱并把原记录标为sent。投递失败保持业务结果有效，队列记retry并保留attempts与受控last_error；重启后可扫描重试。收件箱与发送确认同次保存，任一步失败不会出现半投递；并发重试和已投递记录重试不新增第二条消息。

普通用户 → “我的通知”查看本人通知，标记已读；重复标记保留第一次read_at。直接GET/POST他人通知编号返回403。admin在日志页点击“重试待发送通知”可扫描全部积压；普通用户不能触发管理重试接口。相关页面禁止缓存，通知不包含联系方式。

最终验收已按UML补齐异步投递：应用启动后台线程每1秒扫描持久队列；after_request与管理员重试只唤醒线程，不等待投递。慢投递不占用业务JSON锁，无请求期间及重启后也能重试；收件箱去重与发送确认仍同次保存。详见ACCEPTANCE.md及notification_worker.py。

### 验证与阅读顺序

第一组全量60项通过（29.322秒）；第二组全量62项通过（30.521秒）。第三组最终回归及Pylint结果见下方。重点测试旧页面、日志权限/组合过滤/敏感信息隔离、超时system日志、日志保留、业务保存失败与失败日志、通知投递失败、进程重建及并发重试、重复投递、已读幂等和越权访问。测试比较业务完整快照，单独检查新增日志及投递元数据，不再把“追加失败日志”误认为业务改变。

优先阅读 operations.check_version → storage.transaction → eventlog.journal_changes/operation_entry → notifications.deliver_pending/deliver_one → operations.register_log_routes；页面为logs.html、notifications.html，测试为test_operations.py与test_notifications.py。

该轮未修改报告或提交；最终验收另补真实HTTP和后台通知测试，见ACCEPTANCE.md。


本轮最终实际命令（在lab3目录执行）：

```bash
/tmp/lab3-media-env-vFOw7j/bin/python -m unittest discover -s code -v
PYLINTHOME=/tmp/lab3-final-pylint /tmp/lab3-media-env-vFOw7j/bin/python -m pylint code/*.py
git diff --check
```

最终67项全量通过（32.770秒）；Pylint为10.00/10、退出码0；git diff --check通过。故障注入时出现的“Operation failure log unavailable”是持续存储失败测试的预期错误输出，不是测试失败。data.json和uploads/仍受.gitignore保护，本轮验证未使用运行数据。

| 需求编号 / 设计依据 | 实现位置 | 验证及仍有差异 |
| --- | --- | --- |
| REQ-004/006/007/012/013/014/018 | operations.check_version、app写路由、商品/交易表单 | 显式旧版本、缺失版本及全部新增修改操作回归通过 |
| REQ-005/008/011/015/017/019 | 原审核/预约/终止/违规协议及新版表单 | 旧审核、违规快照、终止旧审批、预约原结果重放继续通过 |
| REQ-020/022/025/030 | storage.transaction、eventlog、operations日志查询 | 成功日志与业务同存；失败日志独立保存；权限、组合查询、系统超时、敏感信息隔离及保存失败通过 |
| REQ-013/019/020/026/029及UML Notification | eventlog队列登记、notifications投递/去重 | 投递失败不撤销业务，重建应用/并发重试、重复投递及写盘失败通过；最终验收已补后台线程，重启无需HTTP触发 |
| REQ-002及UML Notification.readAt | 本人通知列表、对象权限及已读POST | 他人查看/标记拒绝，本人重复已读不改变第一次时间；已读交互细节为本轮实现选择 |


## 最终验收与运行边界

建议Python 3.12，当前验收为3.12.3。按开头命令运行 `python code/app.py` 即自动启动同进程后台通知线程；退出服务器时停止线程。只部署单个服务进程，勿用多进程共享JSON。`create_app(..., notification_worker=False)` 仅供确定性测试或显式手动投递使用，生产默认开启。线程每秒扫描磁盘pending/retry，不只依赖请求唤醒；进程重启继续重试。站内通知页在异步投递尚未结束时可能需要刷新。

最终70项全量测试通过，包含真实HTTP、独立子进程无请求恢复和慢投递非阻塞测试；Pylint 10.00/10（退出码0）。完整33条需求矩阵、任务书核对、行数和打包边界见ACCEPTANCE.md。当前PDF仍是旧版，等待学生更新后再生成最终ZIP；本轮不代写报告。
