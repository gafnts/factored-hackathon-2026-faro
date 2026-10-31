# ADR-0002: Mirror the organizer dataset into pinned snapshots

## Status

Accepted (2026-09-26).

Amended as built, each correction applied in the section it names:

- 2026-10-30: the organizers' dataset can no longer be downloaded, so `make data` restores the pinned snapshot from a copy, checked against the lock, and the data bucket survives a teardown while it holds a snapshot or an export ([Decision](#decision), [Consequences](#consequences)).

## Context

[ADR-0001](0001-deploy-to-us-east-1.md) puts every stack in us-east-1, while the dataset stays in the organizers' bucket in us-east-2. Five forces shape how the project should read it:

- **The dataset sits in another account, behind shared keys.** The bucket belongs to the organizers' account (157725502942) in us-east-2 and is readable with one IAM user's access keys, printed in the dataset dictionary with a request not to share them beyond participants. Reading it at runtime would put those keys in CI and in the deployed prototype, and the prototype would break the day they are rotated or revoked, which may happen once the event ends.
- **The source changes.** On 2026-09-26, `data/` held 7,671 CSV files (5.35 GB): six dimension and reference tables as single files, and seven fact tables partitioned as `year=/month=/day=` (1,097 days each, 1,083 for `campaign_sends`). Next to it, `data_backup_20260831/` holds an earlier publication in which `customers.csv`, `products.csv`, and `service_agents.csv` differ in size, and `call_transcripts` and `satisfaction_surveys` are missing. The dictionary also announces late-arriving partitions and schema changes.
- **Past versions can't be recovered from the source.** The reader key can list and download objects and see their ETags, but not read the bucket's configuration, so a version we didn't keep may be gone for good.
- **A server-side copy is impossible.** Copying between buckets in different accounts needs one principal allowed on both, which would take changes to the organizers' IAM or bucket policy. The bytes have to stream through something that holds both sets of credentials.
- **The scoring rewards stable inputs.** Data preparation must be repeatable (DML-01), with lineage (DML-04) and a freshness policy (DML-05); baseline and proposed system must run on the same held-out workload (EVL-01); credentials stay out of the public repository (SEC-03); and setup must be reproducible (OPS-07).

## Decision

Copy the organizers' `data/` prefix into a bucket this project owns in us-east-1, and use the organizers' keys for that copy only. Everything downstream (ETL, evaluation, CI, the deployed prototype) reads the copy with the project's own roles.

Each copy is an immutable snapshot, and a lock file committed to the repository pins the snapshot the code uses; every copy of it, on a laptop or in the bucket, is checked file by file against the lock. The snapshot pinned on 2026-09-26 is the last one: the organizers' dataset can no longer be downloaded, so there is no newer data to adopt. Re-running a restore or an upload that already matches transfers nothing, and pipeline outputs and evaluation reports record the snapshot they ran on.

The bucket lives outside the per-environment stacks, so destroying an environment never deletes the data, and Terraform won't delete the bucket while it holds a snapshot or an export, so a full teardown leaves it in place too.

In practice:

- **The bucket.** `banking-agent-data-<account-id>-us-east-1-an`, in the account's regional namespace, has a Terraform root of its own, `infra/dataset/`, applied with admin credentials like the deploy roles in `infra/iam/`. Its policy rejects any write without `If-None-Match`, so an object, once written, can't be overwritten. `force_destroy` is off, so `make dataset-destroy` stops while the bucket holds anything.
- **The lock.** `dataset.lock` lists every file with its size, its source ETag, and a SHA-256 computed when the snapshot was downloaded. The snapshot ID is the first 16 hex characters of a SHA-256 over the sorted key, size, and SHA-256 of every file, so it names the content, not the source or the day it was copied.
- **`make data`** checks the local copy in `data/snapshots/<id>/` against the lock: every file's size and SHA-256. `make data FROM=<folder>` restores it from a copy: it refuses a folder that lacks any of the lock's files or holds one with the wrong size, copies only the files the lock lists, checks each one's SHA-256 before putting it in place, and skips files already in place and intact. The lock's keys follow the layout of the organizers' `data/` prefix, so any copy of it works.
- **`make snapshot`** uploads that copy to `snapshots/<id>/` with each file's SHA-256, which S3 checks on arrival, and writes the lock there last as the completion marker. A rerun on a complete snapshot transfers nothing.

## Alternatives considered

**Read the organizers' bucket directly.** No copy to maintain, but the shared keys would have to live in CI and in the deployed prototype, every read would cross accounts and regions, and nothing would stop the data from changing under a running evaluation.

**Copy once and never again.** A run-once guard either misses late-arriving partitions or, the first time someone forces a re-sync, silently changes the evaluation baseline. A lock makes both cases visible.

**Keep the data only on laptops.** `aws s3 sync` into `./data/` is the quickest start, but CI and the prototype can't read a laptop, and two people can end up holding different bytes with nothing to tell them apart. A local copy is still useful for exploration, as long as it comes from the pinned snapshot.

**Pin object versions instead of copying snapshots.** Versioning plus a list of version IDs would pin the data without storing a second copy, but DuckDB and Athena read paths, not version IDs, and an extra 5 GB snapshot costs a few cents a month.

**Server-side copy tools.** `aws s3 sync` between buckets, S3 Replication, S3 Batch Operations, and DataSync all need a grant on the source bucket that only the organizers could give.

## Consequences

Positive:
- The organizers' keys are needed only where the copy runs; nothing deployed and nothing in the repository holds them.
- The prototype and every pipeline keep working if those keys are rotated or revoked.
- When the source went away, the bucket's copy and the lock kept the snapshot: every file is still checked against the lock, and any copy of it can rebuild `data/`.
- Every run reads the same bytes until the lock changes, and a change to the data arrives as a PR like any other change.
- Pipelines read in-region, from the same Region ADR-0001 chose for everything else.
- The snapshot ID traces a number in an evaluation report back to the exact raw files.
- The local copy used for exploration holds the same bytes as the bucket copy, checked file by file.

Negative:
- An upload streams about 5.35 GB from the machine running it: minutes on a datacenter link, tens of minutes on a home connection.
- More moving parts than reading the source directly: a bucket with its own Terraform root, a copy module, and a lock file.
- Each snapshot duplicates the dataset in storage, at a few cents a month.
- A clone in another account can rebuild the locked snapshot only from a copy of the organizers' dataset, since it can no longer be downloaded. Without one, a clone can't run the pipeline, the analysis, or the eval sets, or fill a stack of its own. We report this as a data limitation (SCP-07); for anyone outside the project, it leaves OPS-07's reproducible setup met only by the checks that run on team-written fixtures.
- `make dataset-destroy` stops while the bucket holds a snapshot or an export, since a snapshot can't be rebuilt; deleting one is a deliberate step outside Terraform.
- The lock adds a 1.5 MB file to the repository, exempt from the large-file hook.
