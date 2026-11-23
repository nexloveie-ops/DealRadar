# Mobile123 爱尔兰 Prepay 优惠采集器

第一阶段交付：Vodafone、Three、eir、Tesco Mobile 的公开 Prepay 促销采集、核验和合并。

只读取运营商公开页面和官方 PDF，不登录、不购买、不修改 Shopify、不发送提醒，也没有创建云资源或定时任务。没有依赖搜索引擎摘要作为最终价格数据；没有绕过验证码或登录限制。

## 查看本次结果

- `data/offers.json`：供 App、服务端和博客生成程序读取的主数据。
- `data/offers.csv`：便于人工筛选，UTF-8 BOM 编码，可用 Excel 打开。
- `data/run-report.json`：各运营商的采集状态、来源下载状态及记录数量。
- `data/excluded.json`：明确排除的翻新、Nearly New、过期及范围不符记录。
- `data/discovery-review.json`：条款目录中新发现的潜在活动链接；**不是已核实优惠**。
- `data/changes.json`：相对上次输出新增、实质变化或本次未观察到的记录。没有通知发送动作。
- `offers.schema.json`：JSON Schema 2020-12 数据规范。

`offers.json` 包含审核区记录。消费者页面必须过滤 `distribution.can_list == true`，并再次检查 `verification.fresh_until`。赠品还必须单独检查 `benefits[].can_publish`；不能直接把所有字段拼到营销文案中。

## 本次业务范围

- 国家 IE，币种 EUR，Prepay，不含 Bill Pay、宽带或商业套餐。
- 关注携号转网、新号码用户及现有客户的手机降价、赠品、返现和赠话费。
- 手机只收录全新设备。Regular price 本身不是促销，不会为了增加数量而收录。
- 不比较套餐月均费用，不把 28 天周期换算成年费。必要激活充值条件仍保留。
- 运营商模块不套用之前 Amazon 的 40% 折扣门槛。
- 没有找到可核实的现有客户专属活动时，不会把转网活动标成现有客户适用。
- `not_specified` 是未知，不能解释成所有客户都符合条件。Tesco 的会员优惠可被查询，但还没有足够证据把所有产品逐一标为新/老客户均适用。

## 可重复运行

需要 Python 3.11+ 和 curl。推荐在虚拟环境中运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python collect.py --cache /path/to/private-prepay-evidence --out data
.venv/bin/python -m unittest -v test_collect
```

离线复现同一批下载证据：

```sh
.venv/bin/python collect.py --cache /path/to/private-prepay-evidence --out replay-data --offline --as-of 2026-11-22T23:00:00+00:00
```

离线复现不会把原始下载时间刷新为现在。超过 24 小时的记录会转为 `stale`，不再作为当前优惠展示。

当前工作区使用的环境为 `work/prepay-venv`，原始证据在工作区的 `work/prepay-evidence`。它们不在发布包内，避免把内部缓存混入公开服务。

## 采集方法与维护边界

1. `sources.json` 保存入口页和证据来源，便于配置及替换。只允许已列明的运营商 HTTPS 域名。
2. HTML 使用 DOM 选择器解析。Tesco 以 `article.phoneItem` 为边界读取会员价、普通价、SKU 和容量，不用全页正则混入导航栏中的 Bill Pay 广告。
3. eir 旧站部分内容放在页面脚本中的 CMS JSON。只解码其公开 JSON 内容，不执行 JavaScript。当前阶段采集的是已核实的通用购机优惠，不把商品目录的 `fromPrice` 擅自解释为携号转网最终价格。
4. Vodafone、Three 的主要活动通过官方 PDF 提取；日期、资格和价格表分别核对。
5. 各来源保留下载时间、最终 URL、HTTP 状态和 SHA-256，内部缓存保存原文，可重放验证。
6. HTTP 有超时、有限重试和同域请求间隔。页面受阻时记录失败，不用第三方旧价补齐。
7. 运营商分别解析、再归一化。同一活动重复出现不会变成多条；不同运营商的同款手机不能去重为同一个优惠。
8. `reviewed-terms.json` 是本次已核对条款的内容指纹。相关 PDF 变化后，记录自动进入审核区。**不要自动更新这些指纹**；应先复核日期、资格和领取条件。
9. 条款目录会继续发现新的链接，放进 `discovery-review.json`。新活动需要添加或调整解析规则后才能自动进入有效数据。当前是可维护的半自动采集器，不是能理解任意新活动的无人维护爬虫。
10. 单一页面结构变化会导致该适配器失败并保留报告。上次有、本次未观察到的优惠保留为 `unconfirmed`，不会误判已过期，也不会继续展示为有效。

11. Vodafone 增加逐机型官网商品数据：先正常 GET 公开 Prepay 页面建立匿名会话，再调用同一官方前端使用的 `/mobile/api/get-products/pay-as-you-go`。不使用用户账号凭证。匿名 cookie 仅放在内部证据缓存，不能打包进公开数据。分页数量与 `xTotalCount` 核对，避免把首屏当成全部。此接口是当前官网内部公开使用的接口，不代表承诺长期稳定的开放 API；接口变化时须维护适配器。
12. Vodafone 商品数据里的普通价和 `Switch & Save` 价格分开读取，不把普通价称为制造商 RRP。99.98999 等浮点尾数按欧分四舍五入。A27 5G 的 €200 优惠按条款指定 128GB；不能与门店页 256GB 的价目拼接。A16 的列表优惠与本轮 PDF 不吻合，留在审核区。

## 为后续博客、App 和云服务准备的数据契约

### 金额与折扣

`*_minor` 全部是欧分整数：24999 表示 €249.99。未知是 `null`，不是 0。

`reference_type` 必须随参考价一起使用：

- `same_provider_non_port_in_price`：同运营商非携号转网手机价，不是历史原价或全市场最低价。
- `same_provider_current_non_clubcard_price`：当前非会员价，不是历史原价。

只有明确存在两种可比较的价格时，才计算折扣百分比。仅有“减 €50”或广告中的“减 €200”不会倒推原价。充值、返现、赠品及赠话费不会混进购机折扣百分比。

### 资格与状态

`eligibility` 拆分客户群、是否必须转网、允许/排除的原网络、会员要求、最低激活充值和简短领取条件。未知布尔值为 `null`。

`status` 可为：
- `active`：证据支持当前活动，且有截止日期。
- `active_end_unknown`：当前页面或条款支持，但没有公布截止日期；每天复核。
- `needs_review`：资格、条款、赠品兑现或其他关键信息不足。
- `expired` / `scheduled`：按活动有效期判断。
- `stale`：证据超过保鲜期。
- `unconfirmed`：上次观察到，本次未得到确认。不可展示成当前有效活动。

`ends_on` 是购买或加入活动的截止日；`claim_by` 是领取截止日；`redeem_by` 是券使用截止日；`support_query_by` 是缺券等问题的申诉截止日。不能混用。

### 展示和提醒

- 未逐个下单验证库存。Vodafone 新增的官网商品数据有明确库存标记时记录 `in_stock`；其他来源保留 unknown。均不能写“保证有货”。
- 保存了部分官方图片链接用于后续素材评估，没有下载或默认取得转载许可；`publish_image` 为 false。
- `affiliate_url` 目前为空，运营商链接不会错误附加 Amazon 标签。
- `content_hash` 只针对实质内容，复查时间变化不会触发价格变化提醒。
- 后续通知以 `offer_id + content_hash + subscriber_id` 幂等去重，发送前再核验。
- 当前所有 `notification_ready` 为 false；需先配置订阅、频率、编辑审核和相应渠道规则。
- 消费者服务应使用不超过 `fresh_until` 的缓存，并在源站变化或采集故障时隐藏过期内容。JSON 静态文件本身不会随时间自动改变，客户端也必须检查时间。
- 原始证据与面向 App 的数据应分开存储；将来可由 Cloud Run 定时执行相同采集命令，再由独立接口服务数据。此交付尚未部署。

## 本轮特别处理的问题

- Three 的 2026-11 活动 PDF 标题给出 2026-11-11 开始，正文一处写为 2025。采用标题日期并保留明确的数据质量标记。
- Three Dunnes 赠券活动的新用户加入期已结束，不能因为后续充值截止在年底就显示活动仍开放。
- eir 当前网页展示赠 €20 话费，但找到的赠话费 PDF 是 2025 年活动。该条进入审核区，不把旧条款套到新广告。
- eir 旧版现有客户页面的 €50 文案与转网条款有歧义；不生成“现有客户购机均减 €50”的结论。
- Tesco 列表广告里的赠品需要进一步核实兑现条款；有已核实会员降价的记录可以展示价格，但赠品字段不允许自动发布。只有赠品、尚无可核实降价的记录整体进入审核区。
- Vodafone 当前 Xiaomi 赠表/耳机活动虽有条款，但未证实适用的手机在 Prepay 渠道可买，未直接当作 Prepay 有效优惠。

## 下一步（尚未执行）

1. 补齐待核实活动的当前条款及赠品申请信息。
2. 按有效优惠生成可编辑的博客草稿，不自动发布。
3. 验证各来源自动访问、展示和图片使用的许可；公开可读取不等于正式开放 API。
4. 根据本地运行报告确定更新频率、故障告警及数据保留策略，再部署到 Google Cloud。
5. App 和通知复用该数据模型，不依赖博客 HTML 反向抓取。
