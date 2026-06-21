# Local Recaps

The `sc recap` commands let an agent capture source context, author restricted
MDX, and store a private local recap under `.git/shortcake/recaps`.

## Create a Branch Recap From Any Git Base

```console
$ git checkout -b recap-demo 2>/dev/null
$ printf 'def demo():\n    return "recap"\n' > demo.py && git add demo.py && git commit -m "feat: demo recap"
[recap-demo <HASH>] feat: demo recap
 1 file changed, 2 insertions(+)
 create mode 100644 demo.py
$ sc recap context main --json > context.json
$ python -c "import json; d=json.load(open('context.json')); assert d['source']['branch'] == 'recap-demo'; assert d['files'][0]['path'] == 'demo.py'; print('context ok')"
context ok
$ python -c "import json, pathlib, yaml; d=json.load(open('context.json')); fm={'shortcakeRecap': 1, 'title': 'Demo recap', 'source': d['source']}; pathlib.Path('recap.mdx').write_text('---\n' + yaml.safe_dump(fm, sort_keys=False) + '---\n\n# Demo recap\n\n<FileMap />\n\n<Diff path=\"demo.py\" summary=\"Adds demo function.\" />\n')"
$ sc recap create --mdx @recap.mdx > created.json
$ python -c "import json, pathlib; d=json.load(open('created.json')); assert d['id']; assert pathlib.Path('.git/shortcake/recaps', d['id'], 'recap.mdx').exists(); print('created ok')"
created ok
$ python -c "import json, subprocess; d=json.load(open('created.json')); out=subprocess.check_output(['sc', 'recap', 'show', d['id'], '--json'], text=True); assert json.loads(out)['title'] == 'Demo recap'; print('show ok')"
show ok
```
