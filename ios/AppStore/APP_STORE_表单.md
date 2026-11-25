# Deal Radar：App Store Connect 填表资料

说明用中文，**要复制进表格的内容都是英文**，放在代码框里，直接复制。
`［……］` 是需要你自己填的地方。

---

## 1. 新建 App（App Store Connect → 我的 App → "+" → 新建 App）

| 字段 | 填写 |
|---|---|
| 平台 | iOS |
| 名称 | `Deal Radar`（如果提示名称已被占用，改用 `Deal Radar Ireland`） |
| 主要语言 | English (U.K.) |
| 套装 ID（Bundle ID） | `ie.dealradar.app` |
| SKU | `dealradar-ios` |
| 用户访问权限 | 完全访问权限 |

---

## 2. App 信息（App Information）

**副标题 Subtitle**（最多 30 个字符）
```
Phone & tech deals in Ireland
```

**类别 Category**
- 主要类别：**Shopping（购物）**
- 次要类别：**Lifestyle（生活）**

**内容版权 Content Rights**：选 **"是，此 App 包含、显示或访问第三方内容"**，因为 App 显示了商店的商品名称和图片。
接着会让你确认"我拥有使用这些第三方内容的必要权利"。商品图片来自商店的公开页面，Amazon 的图片通过联盟计划使用。勾选之前请你自己确认这一点，不确定的话先咨询律师。

**年龄分级 Age Rating**：所有问题都选 **"无 / None"** 或 **"否 / No"**。
- "Unrestricted Web Access（无限制网页访问）"选 **No**：App 不内嵌浏览器，链接都用手机自带的浏览器打开。
- 结果应该是 **4+**。

**版权 Copyright**
```
© 2026 Lei Zhang
```

**欧盟数字服务法 Trader Status（交易商身份）**：App 在欧盟上架必须申报。
- 如果 Deal Radar 是以公司（比如 Mobile123）名义经营、有营利目的，选 **Trader（交易商）**，需要公开**地址、电话、邮箱**，会显示在 App Store 页面上。按你给的信息：地址 `69 Mespil Road, Dublin 4, Ireland`，邮箱 `info@dealradar.ie`，电话需要你自己填。
- 如果是个人、非商业，选 Non-trader。
- 因为有联盟佣金、以后还要收费，**大概率应该选 Trader**。

---

## 3. 价格与销售范围（Pricing and Availability）

- 价格：**Free（免费）**
- 销售范围：只选 **Ireland（爱尔兰）**（内容只针对爱尔兰的商店）。以后需要再加英国等地区。

---

## 4. App 隐私（App Privacy）

**隐私政策网址 Privacy Policy URL**（必填，网站上要先有这个页面，见第 8 节）
```
https://www.dealradar.ie/privacy
```

**数据收集问卷**：第一个问题"你或第三方合作伙伴是否从此 App 收集数据？"选 **是**。然后勾选下面这些：

| 数据类型 | 用途 | 是否与用户身份关联 | 是否用于追踪 |
|---|---|---|---|
| **联系信息 → 电子邮件地址**（登录账号） | App 功能 | 是 | 否 |
| **标识符 → 用户 ID**（Firebase 账号编号） | App 功能 | 是 | 否 |
| **使用数据 → 产品交互**（会员点了哪些优惠） | App 功能、分析 | 是 | 否 |
| **用户内容 → 其他用户内容**（搜索词、关注的商品链接和目标价） | App 功能 | 是 | 否 |

- **不要勾**：位置、联系人、照片、健康、财务信息、购买记录、浏览历史、广告数据。
- "追踪（Tracking）"全部选 **否**：我们不跨 App 追踪用户，也不和广告商共享数据。Amazon 联盟链接只是带一个联盟编号，不属于苹果定义的追踪。

---

## 5. 版本页面（1.0 版准备提交）

**宣传文本 Promotional Text**（最多 170 个字符，以后随时可改，不用重新审核）
```
Real discounts from Irish phone networks, shops and Amazon.ie, checked twice a day. Watch a product and get an alert when it hits your price.
```

**描述 Description**
```
Deal Radar finds real discounts on phones and tech in Ireland, so you don't have to check every shop yourself.

We check prices twice a day at the big Irish phone networks and shops, plus Amazon.ie and Mobile123, and show the old price next to the new one so you can see the real saving.

FIND DEALS FAST
• Hundreds of discounted phones, headphones, laptops, tablets, watches, gaming gear and more
• Filter by network or shop, category, price and discount
• Sort by biggest discount, biggest saving or lowest price
• Every deal says when its price was checked and what the old price means

YOUR OWN DEAL LIST
Sign in with Apple or Google to:
• Follow only the networks and shops you use
• Add search words like "PS5" or "Garmin" and see every matching deal, from any shop
• Watch a product: paste its link and set a target price or discount

ALERTS
• Price alerts when a product you watch reaches your target
• New deals for your search words, each evening
• A short daily summary of new deals
No more than one notification per price check, and none at night. You choose which ones you get.

HONEST PRICES
Prices come from each shop's own website. Some links to Amazon are affiliate links: if you buy, Deal Radar may earn a small commission at no extra cost to you.

Deal Radar is independent and isn't run by any of the networks or shops it lists.
```

**关键词 Keywords**（最多 100 个字符，用英文逗号分隔，不要空格。苹果不允许用别人的品牌名，所以不放 Amazon、Vodafone 这些）
```
deals,discount,price drop,price alert,bargain,sale,offers,phone,tech,gadgets,ireland,irish,savings
```

**技术支持网址 Support URL**（必填，网站上要先有这个页面，见第 8 节）
```
https://www.dealradar.ie/support
```

**营销网址 Marketing URL**（可选）
```
https://www.dealradar.ie
```

**截图**：用 `screenshots/` 文件夹里的图片，上传到 **iPhone 6.9 英寸显示屏** 那一栏。只要传这一个尺寸，其他尺寸苹果会自动缩放。

**构建版本 Build**：在 Xcode 里上传后，回到这一页选择（见第 7 节）。

---

## 6. App 审核信息（App Review Information）

**登录信息**：取消勾选"需要登录"。App 不登录也能浏览全部优惠；审核人员想测试会员功能，可以直接用 Sign in with Apple。

**联系信息**：填你的名字、电话、邮箱（只给苹果审核看，不公开）。

**备注 Notes**
```
Deal Radar lists phone and tech deals in Ireland collected from the public websites of Irish phone networks and shops, Amazon.ie and Mobile123. Browsing needs no account.

To test member features, sign in with Apple (or Google) from the My deals or Settings tab. Then:
- Settings: follow or unfollow shops, add a search word (e.g. "JBL"), and watch a product by pasting a product link from amazon.ie, harveynorman.ie, expert.ie, powercity.ie or mobile123.ie.
- My deals: shows deals matching what you follow.
- Settings > Delete account removes the account and all its data.

Notifications are optional: price alerts for watched products, new deals for search words, and a daily summary of new deals, at most one per price check and none at night.

Tapping a deal opens the shop's page in the device's default browser. Some Amazon links carry our Amazon Associates tag.
```

---

## 7. 上传构建版本（在 Xcode 里做）

1. 打开 `ios/DealRadar.xcodeproj`。
2. 左上角设备选 **Any iOS Device (arm64)**。
3. 菜单 **Product → Archive**，等几分钟。
4. 弹出 Organizer 窗口后，点 **Distribute App → App Store Connect → Upload**，一路默认点下一步。
5. 上传后大约 10–30 分钟，构建版本会出现在 App Store Connect 里，回到 1.0 版本页面选择它。
6. **出口合规（Export Compliance）**：问到加密时选 **"无 / 只使用标准加密"**（App 只用 HTTPS）。代码里会加上声明，以后就不会再问了。

---

## 8. 网站上要先有的两个页面

- `https://www.dealradar.ie/privacy`：隐私政策，内容见 `PRIVACY_POLICY.md`。
- `https://www.dealradar.ie/support`：技术支持，内容见 `SUPPORT.md`。

这两个页面我可以加到网站上，需要你确认（见回复）。
