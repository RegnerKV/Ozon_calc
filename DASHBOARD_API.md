# RC3: independent advertising observations

Base: 44434e030dd2c2de123356ff3c06da370fd723d0 (v0.3.0-rc.3).
The four existing sections remain. No separate advertising or sales-growth section.
Financial calculations and reconciliation are unchanged; observations use their own SQLite table.

In Overview select dates, expand “Рекламные показатели товара · отдельно от финансов”, and select an article.
The existing encrypted Performance connection is reused. Reports are generated sequentially for all eligible historical campaigns, regardless of current product membership. An unknown payment type stays unknown. Continue the pending export using the same button; pending UUIDs survive restarts. One click advances up to three completed reports and stops at the first pending report. The supported live period is up to 31 days. Do not start another article/period while an export is pending.

The local v3 diagnostic import is optional and does not perform API diagnostics again. It stores only normalized target observations, without the original uploaded report, connection metadata or owner reference values. Do not commit or upload personal JSON files to GitHub. Diagnostic samples always remain partial; changing the date filter does not reuse a mismatching snapshot.

Known clicks and cart additions are sums of present values, with missing-row counts. These are observations, not certified period totals. Missing values stay unknown. Expense is labelled as reported, never CPC unless payment is explicitly confirmed. Orders and model orders remain per report because attribution can overlap. No advertising conversion uses all-source realized units. No advertising expense is deducted from profit again.

A report-generation request with uncertain outcome blocks further generation rather than submitting duplicate exports. Errors and unknown report states retain previous observations. Failed exports require investigation rather than silent retry or zero replacement. Completed runs are retained; automatic refresh/re-export of the same period is deliberately not performed.

Validation: existing RC3 financial/UI tests plus synthetic tests of report attribution, unknown payment, missing values, persistence, resumed export, exclusive exports and four-section navigation. Windows workflow builds the real EXE and checks UI, DPAPI, one instance and restart persistence; the synthetic advertising fixture is also checked in the bundled app.
