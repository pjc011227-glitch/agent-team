import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_team.chat_service import (ChatError, assemble_history, generate_reply,
                                     pick_agents, reply_to_message, validate_endpoint)
from agent_team.server import create_app
from agent_team.storage import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def test_default_agents_demo(self):
        self.assertEqual(len(self.store.agents()), 3)
        self.assertEqual(len(self.store.credentials()), 1)

    def test_keys_are_encrypted_and_redacted(self):
        c = self.store.create_credential('my key','deepseek','https://api.deepseek.com','top-secret-123')
        self.assertNotIn('api_key', c)
        self.assertTrue(c['has_key'])
        self.assertNotIn('top-secret-123', Path(self.tmp.name, 'agent-team.sqlite3').read_bytes().decode('latin1'))
        self.assertEqual(self.store.credential(c['id'])['api_key'], 'top-secret-123')

    def test_group_cooperation_and_mentions(self):
        agents = self.store.agents()
        convo=self.store.create_conversation('test','group','cooperate',[a['id'] for a in agents])
        targets=pick_agents(convo,agents,'大家来做个计划')
        self.assertEqual(len(targets), 3)
        self.assertEqual(len(pick_agents(convo,agents, '@'+agents[1]['name']+' 帮我',)), 1)
        result=reply_to_message(self.store,convo['id'],'测试这次合作')
        self.assertEqual(result['replies'],3)
        msgs=self.store.messages(convo['id'])
        self.assertEqual([m['sender'] for m in msgs],['user','agent','agent','agent'])

    def test_mention_mode_one_unless_explicit(self):
        agents=self.store.agents()
        convo=self.store.create_conversation('test','group','mention',[a['id'] for a in agents])
        self.assertEqual(len(pick_agents(convo,agents,'你好')),1)
        self.assertEqual(len(pick_agents(convo,agents,f'@{agents[0]["name"]} @{agents[1]["name"]} 你好')),2)

    def test_endpoint_rejects_unsafe_urls(self):
        for address in ['http://example.com/v1','https://user:pass@example.com',
                        'file:///etc/passwd','http://192.168.1.1/v1', 'https://example.com/?token=oops']:
            with self.subTest(address=address), self.assertRaises(ChatError):
                validate_endpoint(address,'compatible')
        self.assertEqual(validate_endpoint('http://127.0.0.1:11434/v1','ollama'),
                         'http://127.0.0.1:11434/v1')

    def test_provider_adapters(self):
        cases=[('compatible', {'choices':[{'message':{'content':'yes'}}]}, 'yes'),
               ('anthropic', {'content':[{'type':'text','text':'okay'}]}, 'okay'),
               ('gemini', {'candidates':[{'content':{'parts':[{'text':'fine'}]}}]}, 'fine')]
        for provider, mock_result, expected in cases:
            with self.subTest(provider=provider),patch('agent_team.chat_service._post_json',return_value=mock_result) as send:
                c={'provider':provider,'api_key':'test-key','base_url':'https://example.com/v1'}
                result=generate_reply(c,'example-model','you are helpful',[{'role':'user','content':'hello'}])
                self.assertEqual(result,expected)
                self.assertTrue(send.called)


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token_patch=patch.dict(os.environ,{'AGENT_TEAM_ACCESS_TOKEN':'token-123'})
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.client=TestClient(create_app(self.tmp.name))
        self.headers={'X-Agent-Team-Token':'token-123'}

    def test_auth_and_redaction(self):
        self.assertEqual(self.client.get('/api/state').status_code,401)
        self.assertEqual(self.client.get('/api/status').json()['protected'],True)
        result=self.client.get('/api/state',headers=self.headers)
        self.assertEqual(result.status_code,200)
        self.assertNotIn('encrypted_key',str(result.json()))

    def test_credential_and_agent_and_skill(self):
        c=self.client.post('/api/credentials',headers=self.headers,json={
            'name':'test','provider':'openrouter','api_key':'secret','base_url':'https://openrouter.ai/api/v1'})
        self.assertEqual(c.status_code,201,c.text)
        self.assertNotIn('secret',c.text)
        cred=c.json()['id']
        skill=self.client.post('/api/skills',headers=self.headers,json={
            'name':'分步分析','description':'分析','instructions':'逐步处理。'})
        self.assertEqual(skill.status_code,201,skill.text)
        agent=self.client.post('/api/agents',headers=self.headers,json={
            'name':'测试员','credential_id':cred,'model':'openai/gpt-4.1',
            'skill_ids':[skill.json()['id']]})
        self.assertEqual(agent.status_code,201,agent.text)
        self.assertEqual(agent.json()['name'],'测试员')

    def test_send_with_demo(self):
        agents=self.client.get('/api/state',headers=self.headers).json()['agents']
        conv=self.client.post('/api/conversations',headers=self.headers,json={
            'name':'双人聊天','kind':'group','mode':'cooperate',
            'agent_ids':[agents[0]['id'],agents[1]['id']]})
        self.assertEqual(conv.status_code,201,conv.text)
        cid=conv.json()['id']
        reply=self.client.post(f'/api/conversations/{cid}/messages',headers=self.headers,json={'text':'你好'})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual(reply.json()['replies'],2)
        msgs=self.client.get(f'/api/conversations/{cid}/messages',headers=self.headers).json()['messages']
        self.assertEqual(len(msgs),3)
        self.assertIn('演示模式',msgs[1]['content'])

    def test_reject_invalid_inputs(self):
        agents=self.client.get('/api/state',headers=self.headers).json()['agents']
        response=self.client.post('/api/conversations',headers=self.headers,json={
            'name':'bad','kind':'direct','mode':'mention',
            'agent_ids':[agents[0]['id'],agents[1]['id']]})
        self.assertEqual(response.status_code,400)
        unsafe=self.client.post('/api/credentials',headers=self.headers,json={
            'name':'unsafe','provider':'compatible','api_key':'secret',
            'base_url':'http://example.org/v1'})
        self.assertEqual(unsafe.status_code,400)


if __name__=='__main__':
    unittest.main()
