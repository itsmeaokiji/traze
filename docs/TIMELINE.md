# Indexed publication timeline

Open **Browse → Timeline** or `/browse?view=timeline` on a running Traze server.
The view uses the same indexed publication summaries, filters and 20-record pages
as the publication table. Opening it does not start collection, discovery or
source refresh. The timeline introduces no additional endpoint or dependency.

## Inspect records

- Read encoded Hemi keystones, publication transaction outputs and their containing
  Bitcoin blocks together. Containing blocks group by exact Bitcoin hash; a height
  alone never merges different blocks.
- Select a keystone to highlight every matching exact payload ID/height on the
  displayed page. Select it again or use **Clear highlight** to reset. Different
  keystone IDs remain distinct even at the same height.
- Open a transaction to inspect its exact publication output. Follow its available
  Hemi evidence link to recompute proof checks. **Back to timeline** preserves the
  original filters and current page cursor through both inspection pages.
- Switch between **Publications** and **Timeline** without losing the shared
  publication cursor. Changing filters or entering/leaving **Published keystones**
  starts a new list. Direct links preserve a current page, not its previous-page
  browsing history; **First page** returns to the first page while retaining the
  applied filters. It is unavailable when already on the first page.
- Use **Clear filters** to remove height and evidence-availability filters and
  return to the first page while keeping the selected view. An empty Timeline
  suggests changing or clearing filters; there is no keystone-selection prompt
  until records are available. Missing indexed matches do not establish that no
  publication exists.
- Use **Refresh index** to read newly stored records without reloading the whole
  website. It keeps applied filters/view and resets pagination. The collection
  summary distinguishes recorded tip age and service-scoped checked coverage;
  neither an index refresh nor zero remaining heights verifies the current chain.

## Meaning and coverage

A solid Hemi connection indicates available associated metadata; a dashed one
indicates pending association or unsupported payload. An encoded keystone label
is not by itself a checked Hemi header association. Unsupported payloads assert
no keystone identity. Unknown/conflicting Bitcoin heights are displayed after
known-height groups without assigning them a position. Bitcoin links identify
exact block hashes on the external reference explorer. Records within each block
sort by encoded keystone height, keystone identity and publication ID; this is
not a transaction-chronology or first-publication ordering.

Groups, highlights and counts concern **only the displayed page**. A keystone or
Bitcoin block may continue on another page. Filtering and uncollected data can
also omit records. An intervening height that is not shown is not proof that no
publication exists there. Spacing is not elapsed time or verified chain ancestry.
Completion feedback reports the number of records on that page. Filter and
first-page actions retain keyboard focus after the read completes.

Summaries do not recompute every proof or establish current canonical membership,
live finality, first publication or complete history. Index storage/retrieval
times remain distinct from historical source observations. See the
[read-API contract](READ_API.md) for exact summary and cursor meanings.

Desktop uses connected lanes; narrow screens stack keystone/transaction pairs
inside each containing-block group. Standard buttons/links support keyboard and
touch input, existing dark/light themes and reduced-motion preferences.
