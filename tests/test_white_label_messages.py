"""Client- and staff-facing API errors must not name Crown Movers: they reach every company's
users, and the branding middleware rewrites pages and static files, not API error JSON."""
import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / 'app'


def http_error_messages():
    for source in sorted(APP.glob('*.py')):
        tree = ast.parse(source.read_text(), str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', '')) == 'HTTPException':
                for arg in node.args[1:2]:
                    for part in ast.walk(arg):
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            yield source.name, node.lineno, part.value


class WhiteLabelMessages(unittest.TestCase):
    def test_api_error_messages_do_not_name_crown(self):
        messages = list(http_error_messages())
        self.assertGreater(len(messages), 300)  # the scan really covers the application
        offenders = [f'{name}:{line}: {text}' for name, line, text in messages if 'crown' in text.lower().replace('crownops', '').replace('legacy-crown', '')]
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
