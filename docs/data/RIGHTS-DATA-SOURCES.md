# Rights-issue (配股) data sources

Checked 2026-11-01 for A-share historical rights terms (per-10-share ratio,
rights price, record date, ex-date, payment window, listing date).

## 1. Current supplier: MaiRui — not available

| Check | Result |
| --- | --- |
| `hscp/jnfh/{code}` (600519 / 600036 / 601288) | HTTP 200; fields `sdate,rdate,give,change,send,line,cdate,edate,hdate` only (cash dividend, bonus, transfer) |
| `hscp/jnpg/{code}`, `hscp/pg/{code}`, `hscp/jnpz/{code}` | HTTP 422 `path or params not served at edge` |
| Documented endpoints | `hscp/jnfh` (dividends) and `hscp/jnzf` (seasoned offering, 增发); no rights endpoint |

Details: `docs/data/SUPPLIER-RIGHTS-COVERAGE.md`,
`.tools/verification-local/review/rights-feed-probe.json`.

## 2. Free source that does provide rights: Sina Finance via AkShare

Interface: `ak.stock_history_dividend_detail(symbol=..., indicator="配股")`,
which parses `vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml`
(table `sharebonus_2`).

Fields returned: 公告日期, 配股方案(每 10 股配股股数), 配股价格(元), 基准股本(股),
除权日, 股权登记日, 缴款起始日, 缴款终止日, 配股上市日, 募集资金合计.

Live verification from this environment (public HTML, no licence):

| Code | Rows | Sample |
| --- | --- | --- |
| 600036 | 2 | 2013-08-23, 1.74/10, 9.29 CNY, ex 2013-09-05, record 2013-08-27, pay 2013-08-28→09-03, listed 2013-09-11 |
| 000002 | 3 | 1999-12-22, 2.727/10, 7.5 CNY; 1997-06-26, 2.37/10, 4.5 CNY |
| 600519 | 0 | `暂时没有数据！` (no rights history) |

Caveats: per-stock HTML scraping; page layout can change; no SLA; Sina's
copyright statement requires written permission for reproduction or other use.
Integration conditions, measured latency/limits, the Eastmoney market-wide
JSON alternative, and a two-source reconciliation are in
[`RIGHTS-FEED-CONDITIONS.md`](RIGHTS-FEED-CONDITIONS.md).

Eastmoney's data centre publishes the same terms in JSON
(`RPT_IPO_ALLOTMENT`, 982 rows back to 1991) and is the better primary source:
one market-wide pull, explicit ex-date, no extra parser dependency. Its legal
statement also prohibits unauthorised reproduction and requires exchange
consent for market-data dissemination.

## 3. Other options

| Provider | Rights terms | Notes |
| --- | --- | --- |
| Tushare Pro | No dedicated rights interface | `dividend` covers cash dividend, bonus and transfer only; 增发 is a separate dataset. Not sufficient for 配股. |
| Eastmoney data center | Page `data.eastmoney.com/xg/pg/` exists (JS data API) | Could be wrapped as a second free source; API shape needs mapping and terms review. |
| Wind / Choice / iFinD | Yes (corporate-action tables) | Commercial terminals; need a licence and field mapping agreed with the vendor. |
| JoinQuant JQData / RiceQuant RQData | Corporate-action datasets | Paid research data; confirm rights fields and redistribution terms with the vendor. |
| SSE / SZSE / CNINFO disclosures | Yes (authoritative announcements) | Best as a verification source: rights prospectus and issuance-result announcements; requires announcement parsing and terms review. |

## 4. Recommendation

1. If a Wind/Choice/iFinD licence already exists, use it — least parsing risk and
   strongest provenance, and the only option that also covers redistribution.
2. For internal research, integrate Eastmoney as primary and Sina as a
   cross-check through a dedicated `rights_terms` research adapter (same
   pipeline as the adjustment factors): publish
   `CorporateAction(action="rights", rights_shares, rights_price, ex_date,
   basis_shares=10, status=implemented)` only when both sources agree, then
   verify on 600036's 2013-09 rights event end to end. Keep provenance and the
   "public page, not licensed for redistribution" limitation explicit.
3. Use exchange/CNINFO announcements to spot-check the parsed terms.

600519 has no rights history, so acceptance must use another instrument
(600036 or 000002) with a dataset covering the event.
