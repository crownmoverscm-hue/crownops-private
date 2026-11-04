PREPARATION ONLY — NO BUILD AUTHORIZATION, NO ACTIVATION

Reviewed BUILD PAYLOAD stays exactly91cea018abf6cf1622532f841a5ae813517dba33.
Controller-preparation branch prep/g5-single-use-91cea01 is separate and starts at91.
The preparation SHA is NOT a replacement app/image candidate. The active source-
only workflow checks gate logic; it cannot acquire an image or deploy Railway.

INERT BUILD CONTROLLER
File:g5/review-ci/g5-one-use-91cea01-v1.yml
After explicit owner approval ONLY, create authorize/g5-91cea01-once-v1 from91 and
add the reviewed controller as.github/workflows/g5-one-use-91cea01-v1.yml in one
activation commit. Do not use the old push-triggered draft. Never merge to main.
The job permits only push/exactrepo/exactauthorizationbranch/run_number1/attempt1.
It uses contents:read, persists no token, no secrets/OIDC/deploy permissions.
GitHub assigns run_number per workflow and run_attempt per rerun. Later pushes
and reruns skip the build job. Concurrency serializes runs. Checkout is pinned91,
not the activation/controller SHA, which is recorded separately in evidence.
A failed/cancelled/skipped first attempt consumes eligibility too; new approval
is needed for a new build. No guaranteed retry or successful completion promised.
Do not rename/delete/recreate the unique workflow/branch to reset eligibility.
An intentional admin rewrite can bypass any repo workflow; this is repeat-build
prevention under the reviewed controller, not tamperproof against repo admins.
Actual GitHub job-gating semantics have NOT been exercised by the model tests.
Before activation verify the unique workflow path has not previously been used;
if it has,STOP; do not reset history. If workflow history inspection unavailable,
record the limit: the run1/attempt1 gate still refuses later numbered runs.

SOURCE-ONLY CHECKS
10 new gate-model/static tests. No original42 app/packaging or47 model rerun.
The controller checks out91, whose original24+10+8 tests/source are unchanged.
No image download/build was performed preparing this controller.

APPROVAL STILL REQUIRED
Owner explicitly authorizes ONE new networked CI build of91, same approvedbase
and35exact wheels, no extraimage/host/dependency/token/resource/publishing. The old
onebuild approval was consumed. US$10 ceiling exists but actualremaining balance
is UNKNOWN; do not assume zero billedmilliseconds means a confirmed cashbalance.
The new build method needs its own focused approval and bounded-endpoint review.
Do not activate until that approval and pre-spend check are recorded.

RAILWAY IMAGE DISTINCTION
CI image is local-only, not published as a Railway-deployable registry image.
Source-based Railway deployment would build91 again on Railway and must record
THAT image/build identity. It is not promotion/reuse of the CI image. This separate
Railway execution remains held; do not smuggle a second build into CI permission.
Within Railway, subsequent phase changes must reuse the recorded initial Railway
build through redeploy(deploymentId), with start/UID variables applied safely and
all previouswriters stopped. If retainedimage identity/reuse cannot be proved,STOP.

PRIMARY DOC
https://docs.github.com/en/actions/reference/workflows-and-actions/contexts
run_number increments on newruns; run_attempt increments on reruns. This enables
read-only-token gating without new persistent credentials or repository writeaccess.
