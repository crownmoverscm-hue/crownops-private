# G5 no-local-CLI / no-new-SSH-key packaging review

**STATUS: REVIEW ONLY — DO NOT DEPLOY.** Created 2026-11-03.

- Branch parent: `098edf780d454b9032684075f77d348ce6a9bb55`.
- Accepted application source is `a56c575dea67924c233934318fc5162ee01f5eef`; its G5 CI overlay is `55b431a3085acac42846179566b8d2e7daf07a8f`.
- Existing `.dockerignore` was added in `098edf7`; keep it.
- New candidate Dockerfile: `g5/Dockerfile.g5-browser-source`, **not** the accepted original offline `g5/Dockerfile.g5`.
- New helper: `g5/fictional_web_entry.py`. Works solely with UID 10001 and the exact disposable G5 IDs and `@example.invalid` fixture model; not an owner-account or production key-custody method.
- GitHub Actions default on push: static contract only. Networked image build is **manual dispatch only**, and must use `approved_networked_wheel_fetch=true` after explicit owner approval. Nothing here deploys Railway.
- The proposed image fetches 35 exact URL/hash-pinned wheel files during a builder-only stage. This is a material change from the accepted **fully offline build**; DO NOT inherit previous G5 build acceptance.
- Candidate wheel and base images remain separately reviewable; document full final image digest if manually built. **The CI-built image is not a deployable Railway image**.
- No application, `config`, `tools/pilot`, original `g5/Dockerfile.g5` or `.dockerignore` changes are permitted by the CI source-integrity gate.

## Specific holds before any Railway commit/deployment

1. DEV/Muse independent focused review of new Dockerfile, key helper and build policy.
2. Owner explicit approval to acquire the 35 hash-pinned wheels during a networked GitHub/Railway image build. Existing US$10 authorization is for the disposable qualification, not a waiver of the changed network policy.
3. Manual GitHub CI build succeeds and its digest and allowed external requests are documented.
4. Railway private GitHub source is accessible, and precise one-service/one-volume root->maintenance->web sequencing and image retention/stop semantics are shown to be safe.
5. Railway dashboard Console supports direct limited access to UID10001 private directory and ciphertext-only file transfer without new SSH keys; otherwise no no-CLI/no-SSH path is established.
6. One verified secure way to stop a writer with positive platform evidence before the next phase; no overlapping writable volume mounts.
7. No real identities, no customer data, no provider keys, no production services, no environment changes until the separate execution gates pass.

**Current platform status before any writes:** four pre-existing staged changes on project `57252bd0-e32f-45aa-9e5b-05bf4a1f8a85`, environment `3fbd111f-5a26-4e1c-80ce-af91fa6247ab`; zero deployments. Any change to that state requires an evidence readback. This branch writes ONLY to GitHub.

**Independent acceptance required:** the author of this branch is not Muse; neither static local tests nor GitHub CI is independent Railway qualification. G5 stays PARTIAL until Railway operations are witnessed and separately reviewed. G6/G7/G8/G9 remain open.
