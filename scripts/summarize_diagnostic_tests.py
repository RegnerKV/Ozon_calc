"""Expose current synthetic CI test evidence in the GitHub job summary."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

path = Path('diagnostic-tests.xml')
if path.is_file():
    root = ET.parse(path).getroot()
    lines = ['## Diagnostic test evidence']
    for suite in root.iter('testsuite'):
        lines.append('Tests: {tests}; failures: {failures}; errors: {errors}; skipped: {skipped}'.format(
            **{key: suite.get(key, '0') for key in ('tests', 'failures', 'errors', 'skipped')}))
    for case in root.iter('testcase'):
        for result in case:
            if result.tag in ('failure', 'error'):
                lines.extend(['### ' + case.get('classname', '') + '::' + case.get('name', ''),
                              '```text', (result.text or '')[-12000:], '```'])
                # CI uses synthetic fixtures only. Expose failures as annotations too:
                # GitHub hides the log/summary endpoints from unauthenticated readers.
                evidence = case.get('classname', '') + '::' + case.get('name', '') + '\n' + (result.text or '')[-6000:]
                evidence = evidence.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                print('::error title=Windows diagnostic test failure::' + evidence, flush=True)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
        stream.write('\n'.join(lines) + '\n')
