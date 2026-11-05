"""Single source of the CrownOps release identity (REL-3).

`main` and the deployed Crown production build both reported 0.12.7-rc6 although
their code differed. Every branch build now states its own version; the content
revision from /api/app-release identifies the exact files.
"""
APP_VERSION = '0.13.0-dev'
