# RC3: independent advertising observations

Base: 44434e030dd2c2de123356ff3c06da370fd723d0 (v0.3.0-rc.3).
The four existing sections remain. No separate advertising or sales-growth section.
Financial calculations and reconciliation are unchanged; observations use their own SQLite table.

In Overview select dates, expand “Рекламные показатели товара · отдельно от финансов”, and select an article.
The existing encrypted Performance connection is reused. Reports are generated sequentially for all eligible historical campaigns, regardless of current product membership. An unknown payment type stays unknown. Start or resume with the report button. While Overview remains open, a fragment advances the pending export every 10 seconds; it can be paused and resumed. Pending UUIDs survive restarts. Each update advances up to three completed reports and stops at the first pending report. The supported live period is up to 31 days. Do not start another article/period while an export is pending.

The local v3 diagnostic import is optional and does not perform API diagnostics again. It stores only normalized target observations, without the original uploaded report, connection metadata or owner reference values. Do not commit or upload personal JSON files to GitHub. Diagnostic samples always remain partial; changing the date filter does not reuse a mismatching snapshot.

The expanded advertising block explains what the observations support, what prevents a profitability verdict, and which evidence to reconcile next. It never recommends changing bids or claims a funnel cause from incomplete attribution. Cart events per 100 clicks use only rows where both metrics are known and are labelled as an event ratio, not an order conversion or shopper percentage. Known clicks and cart additions are sums of present values, with missing-row counts. These are observations, not certified period totals. Missing values stay unknown. Expense is labelled as reported, never CPC unless payment is explicitly confirmed. Orders and model orders remain per report because attribution can overlap. No advertising conversion uses all-source realized units. No advertising expense is deducted from profit again.

A report-generation request with uncertain outcome blocks further generation rather than submitting duplicate exports. Errors and unknown report states retain previous observations. Failed exports require investigation rather than silent retry or zero replacement. Completed runs are retained; automatic refresh/re-export of the same period is deliberately not performed.

Validation: existing RC3 financial/UI tests plus synthetic tests of report attribution, unknown payment, missing values, persistence, resumed export, exclusive exports and four-section navigation. Windows workflow builds the real EXE and checks UI, DPAPI, one instance and restart persistence; the synthetic advertising fixture is also checked in the bundled app.
