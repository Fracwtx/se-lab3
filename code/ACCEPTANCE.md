# 实验三最终验收记录

核验日期：2026-11-24。环境：Python 3.12.3、Flask 3.1.2、Pylint 3.3.8、Pillow 12.0.0；依赖检查 `pip check` 无冲突。

本记录是代码验收证据，不代替学生实验报告。单进程 Flask/Jinja/JSON，无SQL或独立前端；所有验收均使用临时JSON和图片目录，未读取实际运行账户资料。

## 逐项核对实验一需求

“已验证”指下列明确列出的功能与回归证据，不代表穷举所有输入或完成未列出的压力/真人测试。“未验证”不等于功能不存在。测试文件位于同目录。

| 编号 | 状态 | 对应实现 / 本轮证据 | 范围和差异 |
| --- | --- | --- | --- |
| REQ-001 | 已验证 | accounts.py；test_accounts.RegistrationTest、HTTPAcceptanceTest | 注册、登录、查重、演示名保留、普通角色、重启登录 |
| REQ-002 | 已验证 | require_role/check_owner、privacy.py；各权限回归和HTTP | 买卖归属、自购、管理员权限、日志/通知隔离 |
| REQ-003 | 已验证 | validate_product、images.py；ProductTest、DeliveryTest、ImageTest | 不完整草稿、必填、金额/图片限制、地点、封面 |
| REQ-004 | 已验证 | submit_product/withdraw_product；ProductTest、CoverageTest | 草稿送审、本人撤回、旧页拒绝 |
| REQ-005 | 已验证 | review_product；ReviewVersionTest、ReviewHistoryTest、HTTP | 通过/驳回/重审、历史、旧审核拒绝；未单独做两名不同管理员的并发测试，仅预置一个管理员 |
| REQ-006 | 已验证 | edit_product/check_editable；ProductTest、ImageTest、VersionTest | 已发布修改重审、预约锁定、版本；未穷举全部字段与预约的并发交叉 |
| REQ-007 | 已验证 | take_off_shelf；OffShelfTest | 下架隐藏、越权/有效预约拒绝、重审恢复 |
| REQ-008 | 已验证 | moderation.py；ModerationTest | 有效预约终止、普通申请关闭、完成历史保留、解除、原子回滚 |
| REQ-009 | 部分实现 | catalog.query_products；SearchTest | 公开列表关键词/分类/金额交集已验证；本人/管理查询尚无状态组合筛选 |
| REQ-010 | 已验证 | catalog.paginate_products、products.html；PaginationTest、ImageTest、HTTP | 每页20条、ID平局次序、条件保留、封面/地点、隐藏详情 |
| REQ-011 | 已验证 | booking.process_booking；BookingTest、ProductTest、HTTP | 版本/价格、快照、唯一有效预约 |
| REQ-012 | 已验证 | cancel_reservation；ReservationTest、CoverageTest | 本人取消、状态与版本拒绝；未穷举所有取消/违规并发顺序 |
| REQ-013 | 已验证 | expire_reservations/transition_reservation；ReservationTest、CoverageTest | 24小时严格边界、确认/拒绝/超时及通知；预约超时仍在访问/操作时处理，无独立超时任务 |
| REQ-014 | 已验证 | complete_reservation；ProductTest、TerminationTest、HTTP | 本人完成、普通申请同步关闭、完成与审批竞争；成交统计未实现 |
| REQ-015 | 已验证 | app/termination/moderation状态守卫；状态与权限回归 | 八种商品、七种预约状态及普通/违规终止路径 |
| REQ-016 | 未验证（规定规模） | RLock/transaction、test_competing_buyers | 双买家竞争已验证；需求规定的20买家及其故障组未验收 |
| REQ-017 | 已验证 | find_booking_request/process_booking；BookingTest、LogTest | 同键先返回原结果；不同内容冲突；无重复预约/成功日志/通知 |
| REQ-018 | 已验证 | operations.check_version、既有审核/预约/处置版本守卫；VersionTest、CoverageTest、ReviewVersionTest | 新增和已有表单协议均回归；失败仅追加日志，业务与处理历史不变 |
| REQ-019 | 已验证 | termination.py；TerminationTest、HTTP | 双方同意核实、驳回、唯一待处理申请、完成关闭、旧审批拒绝、双方通知 |
| REQ-020 | 已验证 | storage.transaction/eventlog；各故障注入及通知测试 | 业务/成功日志/通知意图同存；投递失败不撤销业务；未模拟所有OS故障类型 |
| REQ-021 | 未实现 | 无统计路由和聚合实现 | 缺商品/分类/日期及成交快照金额统计 |
| REQ-022 | 已验证 | eventlog/operations、logs.html；LogTest、CoverageTest、HTTP | 白名单日志、组合筛选、只读、失败留痕、system超时；不清理旧日志（以历史样本验证保留，非真实等待180天） |
| REQ-023 | 未验证 | 有公开查询实现 | 未做指定硬件/1万商品/20会话/100次性能验收 |
| REQ-024 | 未验证 | 有单进程锁内并发控制 | 未测20买家抢1件/买20件的时间指标，不能从单元测试时长推定达标 |
| REQ-025 | 已验证 | Werkzeug密码哈希、白名单日志；RegistrationTest、LogTest、HTTP | 相同密码两账号哈希不同、无明文、接口/日志不返哈希 |
| REQ-026 | 部分实现 | storage原子替换；跨进程读取、保存失败、后台重启回归 | 正常重启和故障回滚已验证；未做业务写盘中强杀进程的完整崩溃实验，不等于整库损坏恢复 |
| REQ-027 | 未验证 | 无真人计时记录 | 未邀请5名首次使用者完成规定任务 |
| REQ-028 | 已验证 | 实现与requirements.txt代码核对 | 无真实支付、退款、物流、聊天等越界集成 |
| REQ-029 | 部分实现 | UUID记录、用户名查重、锁内唯一预约/申请；相关回归 | 用户以唯一username为关联键，没有UML独立userId字段；约束只支持单服务进程 |
| REQ-030 | 已验证 | counterparty_contact、处置页、通知对象权限；ContactTest/ModerationTest/TerminationTest/HTTP | 仅参与者及有效处置职责管理员可取联系信息；终态限制、公开页/日志不暴露 |
| REQ-031 | 未实现 | 无定期备份和7份轮转实现 | 不能以原子JSON保存冒充每日备份 |
| REQ-032 | 未实现 | 无整库恢复流程 | 未承诺30分钟恢复或24小时损失窗口 |
| REQ-033 | 未验证 | 有HTML/CSS与原型响应式样式 | 浏览器控制不可用，未按1366×768与390×844实际Chrome逐页验收 |

## 实验二UML对应与本轮修复

- `UML/` 的7份puml和7份PNG仍与实验二快照逐字节一致；本轮未改画原设计。
- 状态图：商品/预约的合法转换落实于app.py、termination.py、moderation.py，状态守卫与JSON事务联动。
- 提交预约序列图明确写“用户响应不等待通知；进程在提交后崩溃时，通知任务仍从数据库扫描待发送记录，不依赖内存回调”。原同步after_request投递不符合，已以失败回归复现，再实现notification_worker.py后台线程。
- 生产create_app默认启动一个守护线程，每1秒扫描JSON持久队列；请求结束只发唤醒信号，管理员重试也只唤醒。运输耗时在业务JSON锁外，收件箱与发送确认仍在同一事务内去重提交。无请求时也能重试，重启扫描不依赖内存事件。
- `NotificationWorkerTest` 验证阻塞投递时业务请求0.5秒内返回且JSON可读；另一测试在独立子进程启动后、没有HTTP请求时恢复retry，重启重放不增加第二条收件箱消息。该0.5秒断言只证明测试夹具的非阻塞关系，不是REQ-023/024性能验收。
- 单元夹具显式关闭后台线程并在请求外手动排空通知，保持原回归确定性；后台时序另用普通FlaskClient和真实HTTP测试，不使用夹具同步补投递来证明异步。
- 类图差异：字典/JSON替代实体/数据库对象；用户以username关联，盐编码在Werkzeug哈希字符串中；无Product.createdAt（统计未实现）；交易版本用商品version聚合；violation_active结合状态承担公开限制。多进程JSON写入不支持。

## 本轮实际执行与结果

在lab3目录：

```bash
/tmp/lab3-media-env-vFOw7j/bin/python -m pip check
/tmp/lab3-media-env-vFOw7j/bin/python -m unittest discover -s code -v
PYLINTHOME=/tmp/lab3-acceptance-pylint /tmp/lab3-media-env-vFOw7j/bin/python -m pylint code/*.py
git diff --check
```

全量70项通过（33.997秒），Pylint 10.00/10且退出码0；差异空白检查通过。持续写盘失败测试故意输出失败日志无法落盘的错误行，不是测试失败。

真实HTTP（并非Flask测试客户端）另可单独复跑：

```bash
PYTHONPATH=code /tmp/lab3-media-env-vFOw7j/bin/python -m unittest test_http_acceptance -v
```

测试启动真实本地TCP服务器、随机端口、独立JSON/图片目录、独立cookie会话；所有版本与预约请求标识从真实页面表单解析：

1. 注册双方 → 不完整草稿 → 带地点/图片送审 → 管理员通过 → 买家预约 → 卖家确认 → 买家完成，商品/预约均completed。
2. 另一商品同样预约并确认 → 参与者申请终止 → 管理员核实双方同意后批准，商品off_shelf、预约terminated、申请approved；按申请编号核对买卖双方各且仅一条批准通知，触发重试后仍两条；各方实际通知页可见，管理员组合过滤日志能找到对应成功事件，普通用户日志访问403。

浏览器控制实际返回 `The browser access host is unavailable`，没有可用浏览器。未进行点击、截图、布局或两个视口Chrome验收；不把HTTP成功当作浏览器验收。

## 最终源码规模

执行 `wc -l code/*.py code/templates/*.html code/static/*.css`，再用Python `splitlines()` 与 `bool(line.strip())` 分组统计。注释/文档字符串仍计入非空行；只统计指定源码文件，不含README、验收文档、UML、报告、运行数据、环境或缓存。

| 分类 | 物理行 | 非空行 |
| --- | ---: | ---: |
| Python实现 | 1503 | 1307 |
| Python测试（含真实HTTP验收） | 2211 | 1992 |
| HTML/Jinja | 168 | 168 |
| CSS | 7 | 7 |
| 合计 | 3889 | 3474 |
| 排除测试后的合计 | 1678 | 1482 |

style.css的1个物理行直接复用实验二原型；forms.css的6行是手写扩展，模板沿用实验二结构后改写扩展。未发现GUI工具自动生成代码。任务书只明确大于1500行且排除GUI自动生成部分，未明确测试、空行、模板、CSS或复用代码如何计入，因此只报告数据，不自行认定课程达标。

## 实验三任务书与打包核对

- UML到实现、源码分类规模、风格检查、运行与Git托管可由上述代码/测试与最终Git核对结果佐证；Python为解释执行，使用Flask启动及真实HTTP验证。
- 大模型辅助已有实现、测试和缺陷修正记录。学生是否已逐行阅读代码、报告是否符合本人经历，不能由自动验收代为确认，仍需学生自行检查。
- 最终提交须是 `实验3_学号_姓名/实验报告.pdf + UML/ + code/`。目录内已有code与完整7对UML；现有PDF仍记录46项测试、2377/2118行和旧提交61cc731，未覆盖本轮功能及异步通知修复，属于旧版报告。
- 报告由学生更新，本轮不改PDF/LaTeX，也不生成最终ZIP。报告更新后再检查其内容、单一顶层目录和包清单；不得直接压缩整个工作目录。
- 暂存/提交仅包含源码、依赖、模板/样式、测试及验收/运行说明与UML说明。排除data.json、uploads/、虚拟环境、缓存、日志、LaTeX构建产物；旧PDF/报告源文件及本地任务书不在本轮暂存范围。
- Git本轮推送和本地/远程main哈希一致性必须在提交后再次实查，最终哈希以最终回复及`git rev-parse HEAD`为准，不在本文件预写自引用提交哈希。

提交前附加核验：暂存区36个变更文件、索引59个文件，程序核对不存在运行数据、上传文件、环境、缓存、日志和报告产物。用`git checkout-index`导出的干净暂存副本（没有data.json和uploads）再次通过真实HTTP两条流程，随后删除临时副本。

另实际执行README的`python code/app.py`，用MARKET_DATA_PATH/MARKET_UPLOAD_DIR指向新临时目录：5000端口返回200，预置user登录成功，草稿与成功日志落盘；服务器已终止，临时JSON/图片目录清理，未使用运行数据。原PDF和LaTeX的SHA-256核对未变。
