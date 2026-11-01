# Rights-issue feed integration conditions (AkShare / Sina / Eastmoney)

Checked 2026-11-01 from this environment (UTC+8) with plain `curl` and
`httpx`; no account, key or cookie was used. Scope: historical A-share
rights-issue (配股) terms for research and backtest corporate actions.

This note answers a narrower question than
[`RIGHTS-DATA-SOURCES.md`](RIGHTS-DATA-SOURCES.md): **what does it actually take
to integrate these sources, and what may we legally do with the result?**

Machine-readable probe output: [`RIGHTS-FEED-CONDITIONS.json`](RIGHTS-FEED-CONDITIONS.json).

## Verdict

| Dimension | Result |
| --- | --- |
| Technical access | Green. Public endpoints, no auth, low latency, 1991+ history on Eastmoney. |
| Data shape | Good. Ratio, price, record/ex/listing dates and payment window are published. |
| Stability / SLA | None. Scraped public pages and an undocumented JSON endpoint; layout or access can change without notice. |
| Licence to redistribute | **Not granted.** Both sites require written permission to reproduce content; exchange market data additionally needs exchange consent. |
| Suitable use | Internal research only, opt-in, with provenance and fail-closed checks. Not a default production source and not for export/redistribution. |
| Commercial / redistribution | Use a licensed vendor (Wind / Choice / iFinD / JQData / RQData) or an authorised exchange/CNINFO feed. |

AkShare is **not** a data licence. Its package is MIT-licensed, but that covers
the Python code only; the underlying pages remain Sina's or Eastmoney's.

## 1. AkShare as a dependency

| Check | Result |
| --- | --- |
| PyPI version / licence | `akshare 1.20.21`, MIT, Python >= 3.11 |
| Direct dependencies | `pandas`, `lxml`, `beautifulsoup4`, `html5lib`, `requests`, `curl_cffi`, `xlrd`, `openpyxl`, `tqdm`, `py-mini-racer` |
| Installed in this project | No. The runtime deliberately carries none of the parsing stack. |
| Rights function | `stock_history_dividend_detail(symbol, indicator="配股")` |
| Market-wide rights function | `stock_pg_em()` (wraps Eastmoney `RPT_IPO_ALLOTMENT`) |

`stock_history_dividend_detail` parses Sina with positional table access
(`pd.read_html(...)[13]`) and renames columns by position. Any added or removed
table shifts the index and can silently select the wrong table. The project's
own adapter should select `table#sharebonus_2` explicitly instead.

Conclusion: do not add AkShare as a runtime dependency just for this feed. Call
the documented public endpoints directly with the existing `httpx` stack.

## 2. Sina Finance (per-stock HTML)

Page:
`https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml`

| Check | Measured 2026-11-01 |
| --- | --- |
| HTTP status / auth | `200`, no auth, no cookie, works without a custom User-Agent |
| Encoding | `Content-Type: text/html; charset=gbk` (decode GB18030) |
| Size / latency | ~50–62 KB, ~0.20–0.30 s per stock |
| Cache header | `Cache-Control: max-age=1800` |
| Layout | `table#sharebonus_2`; 10 data columns |
| Coverage checks | 600036: 2 events (2010, 2013); 000002: 3 events (1991, 1997, 1999); 600001 (delisted): 1 event (2000); 600519: no rows (`暂时没有数据！`) |
| Rate limiting | No 429/block across 8 sequential requests; no documented quota |
| `robots.txt` | `vip.stock.finance.sina.com.cn/robots.txt` → 404; `www.sina.com.cn` → `User-agent: * / Allow: /` |

Published fields: 公告日期, 配股方案(每 10 股配股股数), 配股价格(元), 基准股本(股),
除权日, 股权登记日, 缴款起始日, 缴款终止日, 配股上市日, 募集资金合计.

Caveats found in real rows:

- Missing dates are placeholders (`1900-01-01`), not nulls.
- The 1991 000002 event disagrees with Eastmoney on record/ex-date (see §5).
- Payment is a **window**; `CorporateAction.payment_date` is a single date, so it
  must stay `None` rather than being invented.
- No native event id; identity must be derived and same-day ambiguity rejected.

### Sina terms

`https://corp.sina.com.cn/chn/copyright.html` (知识产权声明) states, in
substance, that Sina owns the site content and that **without Sina's prior
written permission nobody may copy, mirror on a non-Sina server, or otherwise
use the content**. `robots.txt` permitting crawling does not grant that
permission.

## 3. Eastmoney data centre (market-wide JSON) — better primary

Endpoint:
`https://datacenter-web.eastmoney.com/api/data/v1/get` with
`reportName=RPT_IPO_ALLOTMENT`.

| Check | Measured 2026-11-01 |
| --- | --- |
| HTTP status / auth | `200`, no auth, no signature, no special header |
| Payload | JSON; `pageSize` is capped at 500, `count=982` rows, 2 pages, ~156 KB total |
| Earliest record | 1991-05-31 (000002) |
| Per-code query | `filter=(SECURITY_CODE="600036")` works; unknown code returns `code=9201 返回数据为空` |
| Latency | ~0.20 s for the full market page |
| Rate limiting | No block observed; AkShare's own transport notes Eastmoney *quote* (`push2`) hosts ban an IP after ~5 pages for 5–40+ minutes, so self-throttling is mandatory |

Useful fields: `PLACING_RATIO` (per 10 shares), `ISSUE_PRICE`,
`EQUITY_RECORD_DATE`, `EX_DIVIDEND_DATE`, `PAY_START_DATE`, `PAY_END_DATE`,
`LISTING_DATE`, `FIRST_NOTICE_DATE`, `TOTAL_SHARES_BEFORE`, `TOTAL_SHARES_AFTER`,
`TOTAL_RAISE_FUNDS`, `SECURITY_NAME_ABBR`.

Why prefer it: one market-wide pull replaces thousands of per-stock HTML
requests, it returns explicit ex-dates, and the JSON needs no new dependency.
It has no `status` field, so `implemented` may only be claimed when a listing
date is present.

### Eastmoney terms

`https://about.eastmoney.com/home/legal` states, in substance: do not impose an
unreasonable or disproportionate load; content may not be copied, reproduced,
redistributed or published without prior written permission; and market data
may not be reproduced or disseminated without the prior written consent of the
exchanges (SSE/SZSE). The data is presented as reference only, with no accuracy
or continuity guarantee.

## 4. Tushare Pro — no rights terms

Checked against the official interface documentation on 2026-11-01. **No live
call was made**: this environment has no Tushare token, so every statement
below is documentation evidence, not a data sample.

| Check | Result |
| --- | --- |
| Dedicated rights interface | None in the published interface list |
| `dividend` (分红送股) | Cash dividend, bonus and transfer only: `stk_div`, `stk_bo_rate`, `stk_co_rate`, `cash_div`, `cash_div_tax`; no rights ratio or rights price |
| `dividend` coverage / gate | From 2000-01-01; 2000 rows per call; **2000 points** required |
| `stk_seasoned` (股票增发) | Seasoned offering (增发), from 1991; 2000 points; `old_sh_pre_plac_ratio` is an SEO priority-allocation ratio, not a rights ratio |
| Announcement dataset | Separate permission (~1000 CNY/year), 10+ years of announcements and PDF links; would require parsing prospectuses, not a structured rights feed |

Access conditions:

- Register an account and use a personal **token**; HTTP endpoint
  `http://api.tushare.pro` with `api_name`, `token`, `params`, `fields`.
- 2000 points ≈ 200 CNY/year: 200 requests/minute, 100,000 calls/day per API.
  5000 points (500 CNY/year): 500 requests/minute, no cap on ordinary data.
- Institutional pricing is 10× the personal list price.

Terms (`Tushare数据服务协议`):

- The service is licensed as **personal, non-transferable, non-commercial,
  revocable, time-limited and non-exclusive**, and **for personal viewing
  only**.
- The account/token may not be shared, transferred, rented or resold.
- Data accuracy, completeness and continuity are not guaranteed.

Verdict: Tushare cannot fill the rights-terms gap. For other corporate
actions its terms are stricter than the public-page route (personal,
non-commercial, viewing only), while still not granting redistribution rights.
It is therefore not a substitute for a licensed vendor in a product.

## 5. Cross-source reconciliation (real rows)

| Instrument / event | Eastmoney | Sina | Agreement |
| --- | --- | --- | --- |
| 600036 2013 | 1.74 / 10 @ 9.29; record 08-27; ex 09-05; pay 08-28→09-03; list 09-11; notice 08-23 | identical | yes |
| 600036 2010 | 1.3 / 10 @ 8.85; record 03-04; ex 03-15; pay 03-05→03-11; list 03-19 | identical | yes |
| 000002 2000 | 2.727 / 10 @ 7.5; record 01-07; ex 01-10; list 02-16; notice 1999-12-22 | identical | yes |
| 000002 1997 | 2.37 / 10 @ 4.5; record 07-11; ex 07-14; list 08-22; notice 06-26 | identical | yes |
| 000002 1991 | ratio 5 @ 4.4; record 05-31; ex 06-01; pay 06-01→06-08; notice 1993-05-24 | record 05-27; ex 06-08; pay 05-28→06-08; list `1900-01-01` | **no** |
| 600519 | no rows | no rows | yes (no rights history) |

Implication: modern events agree, but the pre-1993 record is unreliable and
placeholders exist. Publishing a rights event therefore requires two-source
agreement on ratio, price, record date and ex-date, and rejection of
placeholder dates.

## 6. Integration conditions (if we proceed)

1. **Opt-in, not default.** Enable with an explicit environment flag and a
   recorded acknowledgement that the source is a public page, not a licensed
   feed. Default state stays `not_connected` in the source profile.
2. **No new runtime dependency.** Eastmoney JSON via `json`; Sina HTML via
   `html.parser` selecting `#sharebonus_2`. No pandas/lxml/AkShare in the app.
3. **Bounded transport.** Per-request deadline, response-size cap, single
   in-flight request per host, >=1 s spacing, bounded retries, honour
   `429/5xx`, and stop on repeated blocks instead of retrying through a ban.
4. **Raw evidence first.** Archive exact response bytes and status before
   parsing, as the existing research acquisition path already requires.
5. **Two-source gate.** Eastmoney primary, Sina confirmation. Any mismatch in
   ratio, price, record date or ex-date fails closed; no "best guess" publish.
6. **Field mapping.** `rights_shares` = per-10 ratio, `rights_price` = issue
   price, `basis_shares` = 10, `action="rights"`, `announcement_date` =
   `FIRST_NOTICE_DATE`, `record_date`/`ex_date`/`listing_date` as published,
   `payment_date = None` (window is not a date), `status="implemented"` only
   with a listing date, `terms_evidence` = public page URL.
7. **Disclosure.** Capability limitations, source profile, report and export
   must say: public page, internal research only, no redistribution, no SLA,
   no native event id, pre-1993 dates unreliable.
8. **Acceptance.** 600036 2013-09-05 (10 配 1.74 @ 9.29, record 08-27)
   end-to-end: publish terms, freeze them into a backtest manifest, run
   `subscribe` and `skip`, and reconcile cash and shares. Daily unadjusted
   bars for 600036 around the event are available from the current supplier
   (22 rows for 2013-08-20…09-18 verified).

## 7. Licensed vendor route — Wind registration and pricing

Checked 2026-11-01 against Wind's own site, a 2026 press estimate and real
public single-source procurement notices. **No Wind account, trial or call was
made**: this section is commercial and contractual evidence, not a data sample.

### 7.1 How an account is actually obtained

| Step | Reality |
| --- | --- |
| Self-service signup | **Does not exist.** No public registration → pay → instant access flow. |
| First contact | Trial-request form or a sales call. Form: `https://wx.wind.com.cn/junitedweb/ApplicationForTrial/ContactUsMobile/windContactUs.html?pType=1&lang=zh` |
| Contacts | Sales/support `+86 400-820-9463` / `+86 021-20518200`, `Service@wind.com.cn`; media `PR@wind.com.cn` |
| Contract | Commercial contract and invoice, normally a single-source (单一来源) public procurement in institutions; lead time is a procurement cycle, not minutes. |
| Terminal binding | The account is bound to a desktop terminal. Data access from code goes through Client API (`WSS`/`WSET`/`WSD`/`WST` on top of the terminal). |
| Server-side / automated use | Client API requires an interactive terminal session; unattended server pulls need a separate WDS / data-API licence negotiated with sales. This is the point that actually matters for QTrade. |
| Trial | Time-limited trial via the form/sales; there is no free tier and no public evaluation key. |

### 7.2 Price evidence

| Evidence | Amount |
| --- | --- |
| Industry standard list price for one base terminal account per year (21世纪经济报道, 2026-01-29) | **39,800 CNY** |
| 中再集团 2025 tender: 17 × WFT + 6 × EDB + L2 (announced 2025-12-25) | 751,744 CNY total → ~44,000–75,000 CNY per terminal-equivalent |
| 四川银行 2025–2026 Wind procurement budget | 2,172,956 CNY incl. tax |
| 深圳市委金融监管局 Wind terminal service, one year | 230,000 CNY |
| 上海市委党校 2026 Wind database | 76,960 CNY |
| Choice (东方财富) / iFinD (同花顺) reference points | ~18,000 / ~14,000 CNY per year |

Reading: **a single base WFT account is ~40,000 CNY/year**, and buying the add-on
modules that matter for quantitative work (EDB, AMS, L2 depth, API) commonly
pushes one seat to **40,000–80,000 CNY/year**; a full institutional package goes
well past 100,000 CNY/year. Price is negotiated, not published.

### 7.3 What this does and does not fix

- **Fixes:** licensed corporate-action terms including 配股, plus L2 depth and
  official membership/delisting intervals — three gaps this project currently
  declares as unconnected.
- **Does not automatically fix:** redistribution. The contract must be checked
  for the right to store, derive and re-display. Internal research, derived
  factors and backtest reports are normally permitted; republishing raw vendor
  values is normally not. Get this in writing before building on it.
- **Cost/benefit:** the free Eastmoney + Sina route in §3/§6 covers internal
  research at zero licence cost but cannot be productised. Wind removes that
  blocker for roughly 40,000+ CNY/year plus a procurement cycle.

## 8. Recommendation

- For **research/internal use**, integrate Eastmoney as primary with Sina as a
  cross-check, behind an opt-in flag, following §6.
- For **any redistribution or commercial display**, do not use these pages.
  Buy a licensed corporate-action feed (Wind / Choice / iFinD, see §7) or use an
  authorised exchange/CNINFO disclosure feed, then connect it through the same
  `rights_terms` capability.

Until the user accepts the internal-research-only constraint, the supplier
gap stays declared: rights acceptance remains on a fixture, and this document
does not claim a connected live feed.