# Very.ie 连锁店优惠采集服务

Very.ie 使用独立的 `very.py` 入口、`extensions/very/` 适配器和 `very.json` 配置，输出沿用连锁店服务的数据格式。原有 Powercity、Expert、Harvey Norman、运营商和 Amazon 采集器没有改动。

## 收录规则

- 手机、笔记本电脑、平板、显示器、存储五个分类的 All Offers；每页200款，每类默认最多3页，并发最多3。
- 按同一商品版本的 `prices.now.basePrice` 和 `prices.now.origPrice` 计算，金额保存为欧分；折扣必须严格大于20%，不使用 `prices.next` 或其他版本的对比价。
- 沿用项目确认的 Very 全新零售规则，记录为 `condition=new_inferred`，排除明确标注翻新、二手、展示机等例外。
- 没有库存信息时 `stock_status=unknown`，不影响收录，不宣称实时有货。
- 对比价标为商家 Was 价，不称为厂家RRP或过去30天最低价。
- 每个版本以 Very 商品ID＋SKU标识，保存版本选项、图片地址、采集时间和价格有效时间。默认24小时过期，若已知价格活动更早结束，采用更早的时间。
- 同一版本冲突、刷新失败或不再满足条件时，旧记录不可继续作为新鲜优惠发布；离线重放保留原始采集时间。
- 不发送通知，不调用LLM，不使用Zyte或ScrapingBee，不需要私人API凭证。

## 单独运行

从项目根目录运行，Python环境需安装现有的连锁店依赖：

```sh
python outputs/retailer-offers-service/very.py \
  --cache /absolute/path/very-evidence \
  --out /absolute/path/very-output
```

默认结果在 `outputs/retailer-offers-service/very-data/`，证据在 `very-cache/`；均与其他店铺目录分开，并排除于提交和构建上下文。

可使用 `--offline` 重放证据，`--as-of 2026-11-24T20:00:00+00:00` 指定验证时间，或 `--disabled` 关闭该来源且不发出网络请求。退出码0表示正常，2表示部分失败但安全结果已保存。

输出：`offers.json`、`offers.csv`、`offers.schema.json`、`run-report.json`、`excluded.json`、`changes.json`。

## 接入现有服务

- `cloud/run_collect.py` 新增独立 Very 采集任务，状态保存到 `state/very/offers.json`，失败不阻断其他采集器。
- `outputs/combine_deals.py` 将 Very 结果归入 `module=retailer`；价格来源标明为“零售商前端商品目录”。现有 `/api/deals?module=retailer` 在部署并成功采集后即包括 Very。
- 保留现有公共页/会员价格门槛：Very 采集门槛严格大于20%，公开页仍遵守后台统一门槛；较小折扣可进入会员数据。
- 后台 Shops 增加 Very 开关，其他设置不变。
- 原 `offers.schema.json` 不变，扩展验证只为 Very 新增独立分支，不放宽其他店铺的规则。

## 验证与边界

在连锁店服务目录运行 `python -m unittest discover -p 'test_*.py' -q`。新测试覆盖版本价格配对、严格折扣边界、非全新例外、未知库存、过期、重放、分页重复、局部失败、禁用和现有规则隔离。

Constructor 来源是 Very 网页公开加载的前端目录入口，不是已签约的第三方数据API。它的字段和访问策略可能变化；失败情况写入报告，需持续监测。测试证明本机可访问，尚未验证 Google Cloud 上的长期运行。代码已接入云端任务，但本次未部署、推送或触发线上采集。
