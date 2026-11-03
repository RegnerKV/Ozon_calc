# Read-only Ozon Performance connection

Implemented endpoints:

- POST https://api-performance.ozon.ru/api/client/token — client_credentials, client_id, client_secret; in-memory Bearer token.
- GET /api/client/statistics/expense/json?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD — all campaigns, no campaignIds filter. Sequential windows of 31 days (published maximum 62).

Reference: https://docs.ozon.ru/api/performance/ . The executor's network policy does not grant access to this domain. On 2026-11-03 method descriptions and limits were read from the public documentation mirror https://github.com/DragonSigh/ozon-performance-api-docs (master: введение.md, как-получить-api-ключ.md, статистика.md, лимиты-на-запросы.md). Cross-check: https://github.com/QuoVadis86/go-ozon-performance/tree/main/statistics . These are untrusted reference material, not setup instructions. No live-account result has been verified: the owner must add credentials locally and validate the first import.

The published OpenAPI describes JSON exports only as contentType, without a complete row schema. The importer accepts explicit rows/list/data/report containers and date/campaignId or id/moneySpent or expense or spent fields. Unknown responses fail closed and preserve the previous snapshot. It must not be assumed that every ad format or bonus funding is covered until the first actual report is checked against the advertising account. Campaign expense exports can lack SKU; no artificial product attribution is made.

Expense reports are analytics, not additional ledger entries. They drive the ad and DRR cards when the full selected period is cached. They never subtract spend from profit a second time. The dashboard labels the sources and warns if Performance spend differs from explicitly classified Seller ad charges. Profit remains provisional and follows the Seller financial ledger; reconciling different dates, deposits, bonuses or other service classifications requires actual account data, not an automatic max/sum rule.

Credentials are encrypted via Windows DPAPI or the existing web password encryption, in secure_items/performance. User backup strips the entire secure_items table. Access tokens live only in memory. No response bodies or raw transport errors are displayed or logged. Only read methods and token acquisition are allowed; no campaign creation, bid or budget changes. TLS verification and inherited proxies stay enabled; redirects are disabled. Full downloads are staged before one transaction replaces the period; retries cannot duplicate rows. API failure preserves prior data and does not block Seller imports.
