# 校园集：审核、查询与预约交易

Flask + Jinja 普通表单，标准库 json 本地存储。复用实验二页面结构和 style.css，无 SQL、ORM 或独立前端工程。

## 运行

推荐在包含 `code/` 和 `UML/` 的提交根目录（当前为 lab3）安装，适用于解压后的目录：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r code/requirements.txt
.venv/bin/python -m pip check
.venv/bin/python code/app.py
```

本轮已在独立 Python 3.12 虚拟环境实际安装 requirements.txt，pip check 无冲突。
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
联系方式只保存，不在公开商品列表或详情展示；当前没有扩展联系方式的交易参与者展示功能。
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

1. 启动上面的 app.py 命令，以 user 登录并提交完整商品；退出后用 admin 审核通过。
2. user 进入“我的商品”，点击“普通下架”。确认状态为“已下架”，公开搜索查不到，原公开详情链接返回404。
3. 点击该商品的“编辑 / 重新送审”。可以“保存修改（保持下架）”；点“再次送审”后进入待审核，不能直接恢复发布。
4. admin 再次审核通过；公开列表和详情恢复，审核历史显示两次通过记录。其他普通用户和管理员不能代卖家普通下架。
5. buyer 预约后，卖家下架请求会被拒绝；待确认和已确认预约都属于有效预约。
6. 准备至少21件审核通过的商品，访问公开列表，按价格或发布时间排序后使用“上一页 / 下一页”。添加关键词、分类和价格范围，检查翻页后表单条件保持不变。
7. 无需手工造数据时，可运行下方分页专项测试：它临时创建45件同值商品和1件隐藏商品，并逐页验证20/20/5条、ID顺序及筛选后的20/5条，不改变演示数据。

```bash
PYTHONPATH=lab3/code /home/szmwtx27906/anaconda3/bin/python -m unittest test_listing.PaginationTest -v
```

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
其他操作仍主要按身份与当前状态校验，尚未实现全部操作的预期版本协议。

审核缺陷演示：管理员打开“商品审核”后保留旧页 → 另一会话用卖家撤回、改价、重新送审 → 原管理员页面点通过或驳回，得到409 → 刷新后看到新版商品，重新审核可正常完成。旧审核 POST 示例也必须补上从页面取得的 expected_version，不能由服务端自动用当前版本代替。

## 时间、存储与兼容

新预约保存 UTC created_at/expires_at，严格以创建后24小时为界；截止时刻起不能确认或拒绝。
页面访问和直接业务请求触发超时检查，交易写入锁内再次检查；waiting→timed_out 时同次保存将商品恢复 approved。
已确认预约不会被确认期限超时。没有后台定时器，无访问时不自动落盘；下次访问/操作处理。
测试通过 app.config['CLOCK'] 注入带时区时间，不实际等待。

默认文件是代码目录中的 data.json，与启动目录无关；MARKET_DATA_PATH 可覆盖（父目录须存在）。
顶层保留 products、reservations、reviews、booking_requests，并兼容新增 users 账号列表。
旧商品列表格式仍兼容，旧 rejected 作为 draft 读取，保留原因。
旧商品缺少分类时默认为“其他”、缺版本时为1、缺发布时间时显示未知；保留已有值，下一次业务写入保存迁移后的完整文档。
不捏造旧审核历史：原 reason/reviewer 仍保留，无法恢复的历史时间不补造；新审核开始追加完整记录。
旧 waiting 缺少截止时间时，有创建时间则从该时间计算，否则从首次访问起算并持久保存24小时窗口。
旧预约没有请求标识，不伪造旧请求记录；同买家新请求仍可关联已有有效预约。

所有写入经过 storage.transaction：同一 RLock 包住读文件→校验→修改→原子保存。
临时文件与原文件同目录，json.dump、flush/fsync 后 os.replace；异常不提交，内容无变化不重复替换。
只支持单服务进程；不同进程不共享此锁。预置账号与默认密钥用于本地演示，SECRET_KEY 可覆盖。

## 阅读顺序

1. accounts.py：validate_registration、register、find_account、current_role、login；理解注册查重、密码哈希和普通用户角色。
2. storage.py：load_data、transaction、_save_data，理解兼容和保存边界。
3. catalog.py：parse_bound、query_products、paginate_products、register_catalog_routes；对应 products.html 的搜索表单和详情。
4. app.py：has_active_reservation、take_off_shelf、edit_product；随后阅读 review_product、review_history；对应 reviews.html。
5. booking.py：parse_booking_form、booking_decision、process_booking、associated_reservation，理解参数规范化、首次结果和重放。
6. app.py：reserve_product、booking_result、expire_reservations、transition_reservation；对应 products.html、booking_result.html、reservations.html。

## 验证

从工作区根目录执行：

```bash
/home/szmwtx27906/anaconda3/bin/python -m unittest discover -s lab3/code -v
PYLINTHOME=/tmp/se-lab3-pylint /home/szmwtx27906/anaconda3/bin/python -m pylint lab3/code/*.py
```

原32项回归继续保留并补上审核表单版本；审核修复完成后33项通过，注册实现后全量36项通过。新增测试覆盖旧审核页面的完整操作链、注册登录、重复/保留用户名、并发注册、新账号权限及独立进程重启后登录。
最终 Pylint 退出码0，10.00/10。测试使用临时数据，不污染演示文件。
原预约测试补上正式表单参数；超时后的旧页面先得到版本失效，再刷新确认成功。
本轮还通过真实 HTTP 检查搜索、隐藏详情、历史权限、页面隐藏字段、预约重放及服务进程重启后的原结果读取。
本轮另以独立临时 JSON 和随机本地端口通过真实 HTTP 检查注册、登录、普通用户权限及旧/当前审核表单；服务和数据已清理。浏览器视觉和点击交互尚未验证。

## 剩余范围

本轮完成 REQ-007 普通下架与重新送审、REQ-010 稳定20条分页。REQ-010 的图片/封面、地点字段仍未实现，不能据此宣称该条全部覆盖。
其他剩余范围：终止申请及审批、违规处置及解除、完整日志与通知，以及此前说明的全操作版本协议。
实验说明未明确测试、HTML/CSS、空行和复用代码的计入规则，需确认；不以扩行代替需求实现。
已初始化 lab3 本地 Git 仓库（main）；本地提交及工作区状态以 git log、git status 为准，远程配置以 git remote -v 为准。实验报告由学生独立撰写。

## 本轮行数

实际执行 `wc -l lab3/code/*.py lab3/code/templates/*.html lab3/code/static/*.css`：

| 类别 | 物理行（含空行） | 非空行 |
| --- | ---: | ---: |
| Python 实现 | 717 | 620 |
| Python 测试 | 1041 | 927 |
| HTML/Jinja | 113 | 113 |
| CSS | 4 | 4 |
| 合计 | 1875 | 1664 |

当前分项为：accounts.py 98行；app.py 364行；booking.py 94行；catalog.py 92行；storage.py 69行；test_accounts.py 134行；test_app.py 508行；test_features.py 227行；test_listing.py 172行。
style.css 1行直接复用实验二原型，forms.css 3行沿用此前实现；HTML/Jinja沿用原型结构扩展。
未发现 GUI 工具自动生成代码。物理行总数1875、非空总数1664均含测试和界面，不能自动视为课程认可的实现行数；需确认统计口径。


## 提交前验收与打包边界

本轮实际启动 code/app.py，MARKET_DATA_PATH 指向独立临时目录，通过真实 HTTP 验证：

- user 保存不完整草稿并送审，admin 通过，buyer 预约，user 确认，buyer 完成；JSON 中商品与预约最终均 completed。
- 另一件已发布商品下架前旧详情链接200，下架后404。
- 通过真实提交/审核创建26件筛选商品，下架其中1件后，带关键词、分类、最低/最高价和排序翻页为20/5条；全部条件保留，无重复遗漏。
- 四个演示账号均能登录，样式资源返回200。验收服务器已停止，临时数据已清理，code/data.json 未用于验收。

浏览器控制返回 `The browser access host is unavailable`，故未完成真实浏览器点击、布局、键盘和视觉验收，不能将上述 HTTP 检查称为浏览器验收。
初次提交前的验收记录：原环境及独立 Python 3.12 新安装环境均32项通过，Pylint均10.00/10；本轮注册与审核修复后的最新回归为上文36项。

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

.gitignore 已排除 .venv、缓存、code/data.json、日志、ZIP 导出文件及课程说明 PDF；本地文件仍保留。
运行数据不需要打包：应用支持缺失 data.json，第一次业务写入自动创建。所有代码、模板、样式、依赖清单、测试和 UML 应保留。
.gitignore 不会自动过滤手工压缩。不要直接压缩整个 lab3，也不要将 .git、.venv、缓存和自己的真实运行数据放入提交包。

待报告和代码均由你检查并提交到本地 Git 后，可在 lab3 根目录导出干净包（替换学号姓名）：

```bash
git cat-file -e HEAD:实验报告.pdf
git archive --format=zip --prefix=实验3_学号_姓名/ -o /tmp/实验3_学号_姓名.zip HEAD
```

第一条确认报告已经进入提交；失败时先补齐报告再执行归档。归档只含已提交内容，需检查报告、code 和 UML 都已提交，且未强行纳入忽略文件。
还需提供远程地址、完成实际 commit/push 并核对远程记录。课程行数口径待确认；本README保留分类统计，不将包含测试和界面的总物理行直接认定为合规达标。
