# 连锁零售店优惠

这是 Mobile123 折扣项目的第二个模块，第一模块已命名为“运营商优惠”。本模块只关注爱尔兰的全新 3C 产品。目前可自动采集的是 Powercity 和 Expert Ireland；Currys、Harvey Norman 被 Cloudflare 拦截，只保留六条参考快照。

## 当前状态（2026-11-22 第二版）

- **Expert Ireland（expert.ie，新增）**：11 个 3C 公开分类（耳机、音箱、笔记本、平板、手机、穿戴、相机、存储、网络、游戏主机、显示器），每类最多 3 页。商品卡直接给出现价、零售商 “Was” 价，个别为 “RRP”。现价低于参考价时计算折扣；参考价不高于现价时不算折扣。只收“Add to Cart”可下单的商品，缺货、预订、翻新、展示机排除。没有库存数字，因此 stock_status 为 `available_to_order`，不是 `in_stock`。
- **Expert 的 Was 价是零售商自己的说法**。爱尔兰/欧盟规定降价宣传应以前 30 天最低价为参考，但本程序无法核实这一点，直到自己的价格历史积累满 30 天。所以这些折扣都是 `needs_review`，`can_list` 仍为 false。
- **Powercity**：12 个公开分类（原 6 个，新增相机、游戏主机、网络、手机配件、存储卡、相机配件）。目录没有 Was/RRP，只保存现价观察，不伪造折扣。条件促销文字单独保存，不叠加进现价。
- **价格历史（新增）**：`data/price-history.json` 保存本程序自己直接观测到的价格，每件商品每天一条，保留 90 天。Powercity 以后可以据此判断降价；如果本程序在前 30 天观测到比今天更低的价格，会标出 `observed_lower_price_in_prior_30d;was_price_claim_needs_review`，公开页面只显示现价不显示折扣；同价持续促销不受影响。离线重放不写入历史，参考快照永远不进历史。
- **Currys、Harvey Norman**：返回 403，响应头为 `server: cloudflare`。Currys 是直接拦截（“Attention Required”），Harvey Norman 要求浏览器 JS 验证（`cf-mitigated: challenge`），连 robots.txt 也被拦。本程序不伪装浏览器、不绕过验证。六条参考快照没有准确读取时间，不能自动刷新或通知。
- **DID Electrical**：公开页面和 Shopify 商品 JSON 在少量请求后即返回 429 `local_rate_limited`，未接入。
- 尚未部署服务器、创建定时任务、更新 App、发送提醒或发布 Blog。

## 访问规则

- 只请求 `sources.json` 列出的 HTTPS 域名。各站 robots.txt 对所有 user agent 禁止的路径（如 Expert 的 `/blackfriday/`、`/search.php`、`/cart.php`）在代码里拒绝请求。
- 同一站点的请求间隔按 `request_delay_seconds`：Expert 遵守其 robots.txt `Crawl-delay: 10`，Powercity 0.7 秒。分页只沿页面自己的 `rel="next"` 链接，并受 `max_pages` 限制。
- 请求为普通 curl，不执行 JavaScript，不使用代理或浏览器伪装。

## 运行

需要 Python 3.10+、curl 和 requirements.txt 的依赖。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python collect.py --cache /absolute/path/private-retailer-evidence
.venv/bin/python collect.py --cache /absolute/path/private-retailer-evidence --offline
.venv/bin/python -m unittest -q
```

因 Expert 的 10 秒间隔，一次完整联网运行约需数分钟。退出码 2 表示至少一个来源被拦截、失败或尚未有适配器，其他来源的有效结果仍会保存。`run-report.json` 中记录真实耗时、逐页来源状态与覆盖范围；程序本身不调用 AI，token 为 0。

## 输出与合并

- `data/offers.json`：商品级标准记录，金额为欧分，按有依据的折扣比例降序排列；无参考价的观察记录排在后面。有历史时附 `pricing.observed_history`。
- `data/offers.csv`：Excel 可直接打开的 UTF-8 BOM 表格。
- `data/changes.json`：与上次运行相比，同一零售商、同一 SKU 的实际观测价格变化；不是“历史最低价”证明。
- `data/price-history.json`：本程序的直接观测历史，不是零售商的价格历史。
- `data/excluded.json`：展示机、翻新机、缺货/不可下单、价格不明的排除原因。
- `data/run-report.json`：真实来源状态与未完成事项。

与运营商模块共享语义字段：provider_id、product、pricing.device_price_minor、reference_price_minor、discount_percent、eligibility、validity、verification、distribution 和 links；以 module_id 区分模块。产品映射先保留“商家 + SKU”，不凭类似名称将不同容量、颜色、套装合并成同一商品。Expert 标题里 “|” 后面的型号保存在 product.model_code，供以后跨商家比对，但不自动合并。

“Was” 是零售商历史价，`manufacturer_rrp` 是制造商建议零售价，两者不能混称“原价”。Currys 参考价的 from/to 是历史售价时间段，保存于 pricing.reference_period，不能当促销截止日期。运费、优惠码、返现、会员和套装条件都需分开核验。

## 下一项实际工作

1. 每天定时运行，积累 30 天价格历史后，才能核对 Expert 的 Was 价，并让 Powercity 出现基于观测的降价。
2. 定好人工审核规则后（全新状态、运费、Was 价依据），再放开 can_list；目前 schema 将 can_list 与通知锁为 false。
3. Currys、Harvey Norman、DID：申请商家授权 feed/API 或加入其联盟计划，不绕过拦截或限流。
4. 完成后再接云端定时更新、统一查询接口与 App。图片授权、零售商联盟链接另行处理；不套用 Amazon tag。
