# Amazon.ie 优惠与联盟对接

Mobile123 折扣项目第三模块。目标是按可修改关键词收集 Amazon.ie 全新 3C 优惠，折扣严格大于 40%（不含40%整），按 ASIN 去重，带上 `mobile123ie-21`，供编辑制作 mobile123.ie 的原创文章。

## 当前推荐：不使用 API 的公开网页采集

```sh
python web_collect.py --cache /absolute/path/private-amazon-web-evidence
python web_collect.py --cache /absolute/path/private-amazon-web-evidence --offline --out /absolute/path/replay-output
python -m unittest discover -p 'test_*.py' -q
```

`config.json` 修改关键词、aliases、enabled 和折扣门槛；`web-sources.json` 修改公开来源、每目录页数、请求间隔、来源更新最大天数与有限官方页面检查次数。已扩为21组词，保留原八组并新增音频、音箱、Amazon设备、存储、充电、电脑、显示设备、外设、网络、智能家居、相机、穿戴和更多品牌。默认扫描 Good Craic Deals 九个目录、每个最多八页，Item.ie 的 spotted 页面，以及 DealDepot 的 Amazon.ie 目录最多十页。目录不足页数时提前停止。不调用 Amazon API、搜索 API 或 AI，不需要 Key。

解析 Good Craic 页面公开嵌入的 JSON（不执行 JavaScript），以及 Item.ie 的商品卡片。只读取优惠事实，不导出用户资料、评论、原文描述或图片。逐条匹配关键词、Amazon.ie 域名和单个商品 ASIN，再检查有效价格对、过期状态、条件优惠和严格大于40%的精确值。折扣显示四舍五入不参与合格判断；省电/性能百分比不参与折扣计算。商品合集没有单个 ASIN 时不把合集最低价套给所有产品；Amazon UK、已标翻新或展示机、过期帖、超过30天未更新的线索默认排除。

同一 ASIN 去重，不同 ASIN 保持独立（容量、颜色可能不同）；不同来源价格冲突时保留证据、标记冲突，不选最高折扣冒充当前报价。Good Craic 的 updatedAt 可能来自帖子活动，不等于价格核验时间；Item.ie 的 data-expires 是报价缓存过期时间，不当作活动截止日期。两种时间都与本次下载时间分开保存。离线重放使用原始下载时间，不能把旧数据变成新报价。

结果保存在 `web-data/`：offers.json、candidates.csv、excluded.json、changes.json、run-report.json。金额为欧分，module_id 与其余模块一致。退出2表示某个来源不可用或结构变化，已有成功来源结果仍可保存。changes.json 中的 not_observed 不表示已证实活动结束；缺失旧条目不混入本轮有效候选。

**这是可复用的候选发现流程，不能绕过全新状态与当前价格核验。** 首轮五个官方页面能打开，但报价区要求将商品加入购物篮；程序没有操作购物篮、登录、验证码或代理。当前全部为 web_candidate（或冲突/陈旧）状态，全新、库存、卖家及运费未确认时不允许发布或通知。CSV、可视化中的金额均是第三方记录，不是 Amazon 实时报价，也不能直接拿去发布带价格的联盟 Blog。

本地读取原始 HTML 留在指定私有证据目录，不包含在交付 ZIP 中；对外只能使用经过复核且符合授权用途的内容。公开来源结构变化时会失败并记录原因，仍需维护。服务器部署、定时执行和 App 接入尚未实现。

## 已实现与实际限制

- 保留八个用户指定关键词并扩为21组，支持别名、增加、删除和停用。目前优先运行公开网页采集。
- API预备模式每组默认五页、每页十项；21组全部启用时最多105次 SearchItems 请求、1050个结果位置，不等于独立或合格产品数。用户暂不使用API，此请求计划不执行。
- 对照官方 Creators API 文档实现 OAuth 3.2 欧区请求、内存令牌复用、逐词分页与 OffersV2 解析。没有凭证，尚未真实调用或验证账户资格。这里只完成本地模拟响应测试，不能声称“API 已接通”。
- 仅接受同一个 featured listing 内的全新状态、现货、EUR 价格与参考价；精确计算后 >40% 才进入复核队列，40%整和39.99%都不合格。SearchItems 条件只是发现线索，不能代替逐报价核验。
- Prime、促销时间、卖家、参考价类型分开保存；不把 RRP、Was、最低价标为同一种“原价”。优惠码与运费未核验时保留未知。
- 旧研究的 13 个 ASIN 可生成链接，但价格来自 2026-11-21 的第三方研究，原始日期不改，不视为本次新发现或实时优惠。原先 18 条未必有 ASIN 的候选保存在 discovery-seeds.json，仅供研究，不能混成独立已核实商品总数。
- 生成不含旧价格、折扣承诺或未经授权图片的原创产品介绍草稿；没有发布到 Shopify。

## 使用

需要 Python 3.10+，安装 requirements.txt 后：

```sh
python collect.py
python -m unittest -q
```

默认只准备数据和请求计划，不访问 Amazon、不读取凭证、不消耗 AI token。输出在 data/：

- offers.json：统一字段与候选状态；所有候选当前 can_list=false。
- request-plan.json：待执行官方请求，不含秘密。
- affiliate-links.csv：只有 ASIN 和带 tag 的链接，不复制 API 商品内容。
- blog-draft-no-prices.html：原创价格无关草稿，需编辑确认商品和版本。
- run-report.json：准备模式/真实调用情况、候选数与未完成事项。

真实调用需在服务端安全提供 `AMAZON_CREATORS_CLIENT_ID`、`AMAZON_CREATORS_CLIENT_SECRET`、`AMAZON_CREATORS_CREDENTIAL_VERSION=3.2`，然后运行 `python collect.py --live`。不要将 Secret 放入配置、Blog、App 或发进会话。当前程序不支持其他凭证版本的认证地址。权限、限额、网络或响应格式失败时退出2，并保留失败状态，不把旧研究改成新优惠。

## 联盟链接与展示

标准商品链接格式：`https://www.amazon.ie/dp/ASIN?tag=mobile123ie-21`。已有官方链接时检查爱尔兰域名与 ASIN，保留 Amazon 原有参数，只统一 tag。不编造 linkId，不声称手工链接与 SiteStripe 字符串完全一样，也不保证订单一定计佣。

草稿注明联盟身份并使用用户主动点击的 Amazon.ie 外链。当前登记渠道是 mobile123.ie；App 后续要独立申请/确认资格。运营商、零售店不会添加 Amazon tag。

## 数据生命周期与待完成工作

1. 用户获得并在服务端配置 API 凭证后，真实测试爱尔兰账户权限、响应格式与调用额度。目前只有文档实现与模拟测试。
2. API 数据只在服务器缓存，设置23小时有效期，不建立持久 Amazon 价格历史，不下载商品图。`python collect.py --purge-expired` 删除 offers.json 中已过期 API 记录；每次运行也先清理。将来上线必须运行定时清理，并在每个查询请求中拒绝过期内容，不能把这个静态目录直接当作长期公开 API。
3. API 商品内容不得进入长期日志、对象存储版本历史、备份或固定 Blog HTML。程序未保存原始响应，也不把 API 标题/价格导出 CSV；发布价格须使用可到期刷新的组件，显示时戳和必要声明。准备阶段生成的静态草稿没有价格或 API 内容。
4. `notification_ready` 始终关闭。Amazon 联盟政策对价格追踪/提醒设有单独要求，未经其同意不得上线。其他商家提醒可另行设计，不据此假定 Amazon 允许。
5. 不将 Amazon Program Content 用于推广其他零售商，也不做未经确认的跨商家价格对比展示。可以用 module_id 在后台组织三部分，但对外用途和权限需要分别处理。
6. 图片与当前价格接入授权来源后再完善 Blog；是否自动发布、App 上架与 Google Cloud 定时任务尚未实现。

## 本次核对的官方资料（2026-11-22）

- https://affiliate-program.amazon.com/creatorsapi/docs/
- https://affiliate-program.amazon.com/creatorsapi/docs/en-us/get-started/using-curl
- https://affiliate-program.amazon.com/creatorsapi/docs/en-us/api-reference/operations/search-items
- https://affiliate-program.amazon.com/creatorsapi/docs/en-us/api-reference/resources/offersV2
- https://affiliate-program.amazon.com/creatorsapi/docs/en-us/locale-reference/ireland
- https://affiliate-program.amazon.ie/help/operating/policies?ac-ms-src=ac-nav
- https://affiliate-program.amazon.ie/help/node/topic/G6253GFSARDQENZR

官方页面存在个别示例/标题笔误，因此实际 API 接入需以账户真实响应复核；不能用通过模拟测试替代线上测试。
