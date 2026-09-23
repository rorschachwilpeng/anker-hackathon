#!/usr/bin/env python3
"""Evaluate the S1 Pro local demo and its persisted ticket effects."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from app.knowledge.types import KnowledgeError
from app.s1pro.flow import S1ProFlow
from app.s1pro.state import S1ProState
from scripts.evaluate import load_conversation_trace


class FailureRepo:
    def __init__(self):
        self.state = S1ProState(confirmed_model='T2080', symptom='吸力变弱')
        self.messages = []

    async def get_conversation(self, number):
        return {'user_id': 'local-demo'}

    async def load_s1pro_state(self, number):
        return S1ProState.from_dict(asdict(self.state))

    async def save_s1pro_state(self, number, state):
        self.state = state

    async def append_message(self, number, role, content):
        self.messages.append((role, content))


class FailureUnderstanding:
    async def interpret(self, message, state):
        return {'symptom_candidate': '吸力变弱'}


class FailureSearch:
    async def find_for_model(self, query, model):
        raise KnowledgeError('vector_unavailable', retryable=True)


async def injected_failure():
    return await S1ProFlow(FailureRepo(), FailureUnderstanding(), FailureSearch()).handle('吸力仍然很弱', 1)


def evaluate_case(case, client):
    result = {'id': case['id'], 'passed': False, 'turns': [], 'expected_tickets': case['tickets']}
    try:
        if case.get('mode') == 'injected_retrieval_failure':
            actual = asyncio.run(injected_failure())
            result['turns'].append({'expected_action': case['action'], 'actual_action': actual['action'],
                                    'expected_tool_calls': case['tool_calls'], 'actual_tool_calls': actual['tool_calls'],
                                    'source': actual['source'], 'ticket_no': actual['ticket_no']})
            result['ticket_count'] = 0
            result['passed'] = (actual['action'] == case['action'] and
                                actual['tool_calls'] == case['tool_calls'] and
                                actual['source'] is None and actual['ticket_no'] is None)
            return result
        conversation_id = None
        numbers = []
        for turn in case['turns']:
            response = client.post('/s1pro/chat', json={'message': turn['message'], 'conversation_id': conversation_id})
            response.raise_for_status()
            actual = response.json()
            conversation_id = actual['conversation_id']
            source = actual.get('source')
            official = bool(source and source.get('url', '').startswith('https://service.eufy.com/'))
            expected_source = turn.get('official_source', False)
            passed = (actual['action'] == turn['action'] and
                      actual['tool_calls'] == turn['tool_calls'] and official == expected_source)
            if actual.get('ticket_no'):
                numbers.append(actual['ticket_no'])
            result['turns'].append({'expected_action': turn['action'], 'actual_action': actual['action'],
                                    'expected_tool_calls': turn['tool_calls'], 'actual_tool_calls': actual['tool_calls'],
                                    'expected_official_source': expected_source, 'source': source,
                                    'ticket_no': actual.get('ticket_no'), 'passed': passed})
        trace = load_conversation_trace(conversation_id)
        result['ticket_count'] = len(trace['tickets'])
        result['conversation_id'] = conversation_id
        result['passed'] = (all(turn['passed'] for turn in result['turns']) and
                            result['ticket_count'] == case['tickets'] and
                            (not case.get('same_ticket') or len(set(numbers)) == 1 and len(numbers) == 2))
        if case['tickets']:
            detail = client.get('/s1pro/tickets/' + numbers[-1])
            detail.raise_for_status()
            handoff = detail.json()
            result['handoff'] = {'product_model': handoff['product_model'],
                                 'symptom': handoff['symptom'], 'completed_steps': handoff['completed_steps'],
                                 'sources': handoff.get('sources', []), 'status': handoff['status']}
            result['passed'] = result['passed'] and (
                handoff['product_model'] == 'T2080' and handoff['symptom'] == '吸力变弱' and
                len(handoff['completed_steps']) >= 1 and
                any(source['url'].startswith('https://service.eufy.com/') for source in handoff.get('sources', [])))
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    return result


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--cases', type=Path, default=root / 'evals/s1pro-cases.json')
    parser.add_argument('--output', type=Path, default=root / 'evals/s1pro-latest-results.json')
    parser.add_argument('--only', help='Run one case by ID')
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding='utf-8'))['cases']
    if args.only:
        cases = [case for case in cases if case['id'] == args.only]
        if not cases:
            parser.error('unknown case ID')
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    with httpx.Client(base_url=args.base_url, timeout=90) as client:
        results = [evaluate_case(case, client) for _ in range(args.repeat) for case in cases]
    passed = sum(result['passed'] for result in results)
    report = {'summary': {'total': len(results), 'passed': passed, 'failed': len(results) - passed},
              'results': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'evaluated={len(results)} passed={passed} failed={len(results)-passed}')
    print(f'report={args.output}')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
