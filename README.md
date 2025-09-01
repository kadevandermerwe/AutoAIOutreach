# Second Brain — v2 (Leads + Outreach)

Deploys:
- Mastering API + UI
- Leads API + UI (YouTube search, compose DM/email, SendGrid email)

After deploying on Render, set env vars:
- leads-api: YT_API_KEY, (optional) OPENAI_API_KEY, SENDGRID_API_KEY, EMAIL_FROM, EMAIL_FROM_NAME, EMAIL_RATE_SECONDS
- leads-ui: NEXT_PUBLIC_LEADS_API_URL = https://<leads-api-host>
- mastering-ui: NEXT_PUBLIC_MASTERING_API_URL = https://<mastering-api-host>

IG/TikTok DMs are not automated (ToS). Email uses SendGrid; authenticate your domain.
