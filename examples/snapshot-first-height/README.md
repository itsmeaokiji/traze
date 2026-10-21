# Recorded snapshot example

This optional example contains public mainnet evidence, not installation state.
It demonstrates the version-3 portable verifier independently of a running Traze
website or public provider. Ordinary website discovery remains a separate flow
where you can choose your own Hemi input.

The recorded route is Hemi **5,200,117** through covering keystone **5,200,125**
and publishing keystone **5,200,225**, with an applicable transaction in Bitcoin
**965,045**. Complete retained interval evidence covers Bitcoin **965,036–965,045**.
The Hemi snapshot is **5,558,131**, observed October 21, 2026, 06:31:03–06:31:06
UTC. Bitcoin observations retain their original October 20 times. This example
does not certify current canonical membership on either chain.

## Verify without accepting assumptions

From the repository root, using Python 3.10+ and no additional packages:

```shell
python -B -m traze_evidence examples/snapshot-first-height/packet --hemi-block 5200117 --hemi-hash 0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd
```

Expected status: `verified_snapshot_evidence`, with
`first_height_under_accepted_assumptions=false`. This checks supplied publication,
history, interval and context bindings. It does not accept external premises.

## Request the conditional assessment

First review [the snapshot assurance contract](../../docs/SNAPSHOT_ASSURANCE.md).
Only if you independently accept its source, historical-profile, branch-validity
and security premises for this exact recorded context, add both caller options:

```shell
python -B -m traze_evidence examples/snapshot-first-height/packet --hemi-block 5200117 --hemi-hash 0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd --accept-policy hemi-snapshot-first-height-assumptions-v1 --accept-context 840184d1dab42688a714b71deba8938cb97958d48191bda32619586df4b03b34
```

Expected status: `verified_conditional_first_height`, at Bitcoin **965,045**.
The eligible-keystone domain runs from 5,200,125 through the snapshot, including
keystones after the publisher. `globally_first_proven` remains false. Historical
execution/deployment, intervening post-publisher ancestry and source authenticity
are not independently established. Future snapshots and unreviewed upgrades are
outside this assessment. Accepting the context is a trust choice, not proof that
its external assertions are true.

## Identities and distribution

- Outer manifest SHA-256: `ee5852fa2eb7dfa74b45a6a35fad69e113ceb51cf0822744e4b4296e8616b8c7`.
- Canonical context SHA-256: `840184d1dab42688a714b71deba8938cb97958d48191bda32619586df4b03b34`.
- Child history manifest SHA-256: `dd2693d9f2d93d7767bd7372b6f2ff1ada67039a2c096eef75bd89361681eeee`.

The packet has three manifests and ten Bitcoin bodies, approximately 15 MB.
It preserves unsigned source observations and exact evidence bytes. It contains
no private cache, database, acquisition ledger or operator-acceptance record.
The verifier accepts policy/context only through explicit caller arguments.

This packet is not automatically imported into a website's local state and does
not enable the snapshot policy by default. Preparing matching context for other
inputs remains separate work. The example's ten bodies are separate from the
17-body catalogue fixture corpus used by the quickstart; they do not change its
expected catalogue counts. No source request is made by either command above.
