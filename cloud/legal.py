"""The privacy policy (/privacy) and support (/support) pages, linked from the App Store listing."""
import html

from flask import Blueprint, Response

bp = Blueprint("legal", __name__)

# Who runs Deal Radar and how to reach them. Shown on both pages.
CONTROLLER = "Lei Zhang, 69 Mespil Road, Dublin 4, Ireland"
CONTACT_EMAIL = "info@dealradar.ie"
UPDATED = "26 November 2026"

PAGE = """<!doctype html>
<html lang="en-IE">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | Deal Radar Ireland</title>
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,700&display=swap" rel="stylesheet">
<style>
:root{{--blue:#3565E0;--ink:#16213A;--muted:#5B6578;--shelf:#EEF1F5;--paper:#fff;--line:#DCE1EA}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--shelf);color:var(--ink);font:16px/1.6 "DM Sans",system-ui,-apple-system,sans-serif}}
a{{color:var(--blue)}}
.top{{background:var(--paper);border-bottom:1px solid var(--line)}}
.top div,.main{{max-width:760px;margin:0 auto;padding:0 20px}}
.top div{{height:72px;display:flex;align-items:center}}
.top img{{height:50px;display:block}}
.main{{padding-top:28px;padding-bottom:56px}}
.card{{background:var(--paper);border-radius:8px;padding:28px 28px 20px}}
h1{{font-size:32px;line-height:1.15;letter-spacing:-.02em;margin:0 0 6px}}
h2{{font-size:19px;margin:28px 0 6px}}
p,li{{color:#2B354B}}
.muted{{color:var(--muted);font-size:14px;margin:0 0 18px}}
ul{{padding-left:22px}}
@media (max-width:560px){{.card{{padding:20px 18px 12px}}h1{{font-size:27px}}}}
</style>
</head>
<body>
<header class="top"><div><a href="/" aria-label="Deal Radar Ireland home"><img src="/static/dealradar-logo.png" alt="Deal Radar Ireland"></a></div></header>
<main class="main"><article class="card">
{body}
</article></main>
</body>
</html>"""


def page(title, body):
    return Response(PAGE.format(title=html.escape(title), body=body), mimetype="text/html")


def contact():
    e = html.escape(CONTACT_EMAIL)
    return f'<a href="mailto:{e}">{e}</a>'


@bp.get("/privacy")
def privacy():
    return page("Privacy policy", f"""
<h1>Privacy policy</h1>
<p class="muted">Last updated {UPDATED}</p>
<p>Deal Radar Ireland ("Deal Radar", "we") runs the website dealradar.ie and the Deal Radar app. This policy explains what we
collect, why, and the choices you have. {html.escape(CONTROLLER)} is responsible for your data. Contact: {contact()}.</p>

<h2>Without an account</h2>
<p>You can browse every deal without signing in. We don't ask for your name, email or location.</p>
<ul>
<li><b>Taps on deals.</b> When you tap a deal we record which deal was tapped, so we know which deals are useful. Without an account this isn't linked to you.</li>
<li><b>Notifications.</b> If you allow notifications in the app, we store your phone's notification token and which notifications you chose, and use them only to send those notifications.</li>
<li><b>On this device.</b> The website remembers which deals you have already seen, in your browser only, so it can mark new ones.</li>
</ul>

<h2>With an account</h2>
<p>When you sign in with Apple, Google or an email link, we keep:</p>
<ul>
<li>your <b>email address</b> (with Sign in with Apple this can be a private relay address) and an account number;</li>
<li>what you <b>follow</b>: networks and shops, search words, and links to products you watch with your target price or discount;</li>
<li>the <b>deals you tap</b>, so you can see your activity and we can improve the service;</li>
<li>your <b>membership level</b>.</li>
</ul>
<p>We use this only to run the service: your own deal list, searches for your words, price checks for products you watch, and the notifications you chose.</p>

<h2>Who processes your data</h2>
<ul>
<li><b>Google Firebase and Google Cloud</b>: sign-in, database, hosting and notifications, in the EU (Belgium) where possible.</li>
<li><b>Apple</b> delivers notifications to iPhones, and provides Sign in with Apple if you use it.</li>
</ul>
<p>We don't sell your data, we don't show ads, and we don't track you across other apps or websites.</p>

<h2>Shops and affiliate links</h2>
<p>Tapping a deal opens the shop's own website or app, which has its own privacy policy. Some Amazon links are affiliate links
(Amazon Associates): Amazon may know you came from Deal Radar, and we may earn a commission at no extra cost to you.</p>

<h2>How long we keep it</h2>
<p>Account data is kept while you have an account. Notification tokens are removed when you delete the app or the token stops
working. Deal prices are not personal data.</p>

<h2>Your choices and rights</h2>
<ul>
<li>Turn notifications on or off at any time in the app's Settings or in your phone's settings.</li>
<li><b>Delete your account</b> in the app (Settings, Delete account) or by emailing {contact()}. This removes your account and everything linked to it.</li>
<li>Under the GDPR you can ask for a copy of your data, ask us to correct or delete it, or object to how we use it. You can also
complain to the Data Protection Commission (<a href="https://www.dataprotection.ie">dataprotection.ie</a>).</li>
</ul>

<h2>Children</h2>
<p>Deal Radar is not aimed at children under 16.</p>

<h2>Changes</h2>
<p>If this policy changes we will update the date above, and tell members in the app or by email if the change is significant.</p>
""")


@bp.get("/support")
def support():
    return page("Support", f"""
<h1>Support</h1>
<p>Need help with the Deal Radar app or dealradar.ie? Email {contact()} and we'll get back to you.</p>

<h2>How often are prices checked?</h2>
<p>Twice a day, around 7am and 7pm Irish time. Each deal says when its price was last checked. The shop's own page always has the final price.</p>

<h2>What does the old price mean?</h2>
<p>For phone networks, the same phone's price when you don't switch or without a loyalty card. For most shops, the shop's own
Was price. Powercity doesn't show one, so we use a higher price we saw there ourselves. For Amazon, the RRP or Was price on Amazon.</p>

<h2>Why didn't I get a notification?</h2>
<p>Check that notifications are on for Deal Radar in your phone's settings and in the app's Settings. We send at most one
notification per price check, none between 10pm and 7am, and only when there's something new.</p>

<h2>How do I watch a product?</h2>
<p>Sign in, open Settings, tap Watch a product and paste a product link from Amazon.ie, Harvey Norman, Expert, Powercity or
Mobile123. Choose any discount, a discount of at least a percentage, or a price below an amount.</p>

<h2>How do I delete my account?</h2>
<p>In the app: Settings, Delete account. If you can't sign in, email {contact()} from the address you signed up with.</p>

<h2>Is Deal Radar run by the shops?</h2>
<p>No. Deal Radar is independent. Some Amazon links are affiliate links: if you buy, we may earn a small commission at no extra cost to you.</p>

<p class="muted"><a href="/privacy">Privacy policy</a></p>
""")
