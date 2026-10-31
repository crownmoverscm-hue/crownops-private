"""No listener starts without explicit later endpoint-execution authorization."""
import argparse
import os
from pathlib import Path
import re
from .isolation import network_none_inventory


def main():
    parser=argparse.ArgumentParser(description='Synthetic endpoint. Mock tests are separate; serving needs explicit owner authorization.')
    parser.add_argument('--serve-approved',action='store_true')
    parser.add_argument('--capability-file',type=Path)
    args=parser.parse_args()
    if not args.serve_approved or os.getenv('CROWNOPS_SYNTHETIC_ENDPOINT_START')!='approved':
        parser.error('Serving is not authorized by the local qualification package')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',os.getenv('CROWNOPS_SYNTHETIC_IMAGE_DIGEST','')):
        parser.error('Reviewed immutable image identity required')
    namespace=network_none_inventory()
    capability=args.capability_file
    if capability is None or capability.is_symlink() or capability.resolve()!=capability.absolute():parser.error('Private capability file required')
    stat=capability.stat()
    if stat.st_uid!=os.getuid() or stat.st_mode & 0o077 or not 32<=stat.st_size<=256:parser.error('Owner-only private capability file required')
    token=capability.read_text().strip()
    from .adapter import create_adapter
    app=create_adapter('/run/crownops-intake-test',token)
    app.state.synthetic.namespace=namespace
    try:
        import uvicorn
        # Numeric loopback is resolved without DNS before the process guard.
        import socket
        listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        listener.bind(('127.0.0.1',18765));listener.listen(32)
        config=uvicorn.Config(app,workers=1,access_log=False,lifespan='off',log_level='warning')
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        app.state.synthetic.close()


if __name__=='__main__':main()
