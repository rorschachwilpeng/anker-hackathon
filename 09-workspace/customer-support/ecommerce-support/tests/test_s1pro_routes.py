from fastapi.testclient import TestClient

from app.main import create_app


class Repo:
    def __init__(self):
        self.statuses={'T1':'待处理'}
    async def list_s1pro_tickets(self):
        return [{'ticket_no':'T1','product_model':'T2080','symptom':'吸力变弱','status':self.statuses['T1']}]
    async def get_s1pro_ticket(self,number):
        if number!='T1':return None
        return {'ticket_no':'T1','product_model':'T2080','symptom':'吸力变弱','completed_steps':['清理侧刷'],
                'feedback':'仍无改善','description':'需要人工跟进','status':self.statuses['T1']}
    async def update_s1pro_ticket_status(self,number,status):
        if number not in self.statuses:return None
        self.statuses[number]=status
        return {'ticket_no':number,'status':status}


class Flow:
    repo=Repo()
    async def handle(self,message,conversation_id=None):
        return {'conversation_id':conversation_id or 7,'action':'clarify_product','reply':'请确认产品',
                'source':None,'ticket_no':None}


def test_s1pro_route_reuses_conversation_and_workbench_shows_summary():
    with TestClient(create_app(service=object(),s1pro_flow=Flow())) as client:
        first=client.post('/s1pro/chat',json={'message':'S1 Pro 不吸了'})
        assert first.status_code==200
        assert first.json()['conversation_id']==7
        second=client.post('/s1pro/chat',json={'message':'是的','conversation_id':7})
        assert second.json()['conversation_id']==7
        assert client.get('/s1pro/tickets').json()[0]['ticket_no']=='T1'
        detail=client.get('/s1pro/tickets/T1').json()
        assert detail['product_model']=='T2080' and detail['completed_steps']==['清理侧刷']
        assert client.get('/s1pro/tickets/missing').status_code==404
        assert client.get('/s1pro/workbench').status_code==200


def test_s1pro_ticket_status_update():
    with TestClient(create_app(service=object(),s1pro_flow=Flow())) as client:
        updated=client.post('/s1pro/tickets/T1/status',json={'status':'处理中'})
        assert updated.status_code==200
        assert updated.json()=={'ticket_no':'T1','status':'处理中'}
        assert client.get('/s1pro/tickets/T1').json()['status']=='处理中'
        assert client.get('/s1pro/tickets').json()[0]['status']=='处理中'
        assert client.post('/s1pro/tickets/missing/status',json={'status':'已解决'}).status_code==404
        assert client.post('/s1pro/tickets/T1/status',json={'status':'随便写'}).status_code==422


def test_s1pro_ticket_status_update():
    with TestClient(create_app(service=object(),s1pro_flow=Flow())) as client:
        updated=client.post('/s1pro/tickets/T1/status',json={'status':'处理中'})
        assert updated.status_code==200
        assert updated.json()=={'ticket_no':'T1','status':'处理中'}
        assert client.get('/s1pro/tickets/T1').json()['status']=='处理中'
        assert client.get('/s1pro/tickets').json()[0]['status']=='处理中'
        assert client.post('/s1pro/tickets/missing/status',json={'status':'已解决'}).status_code==404
        assert client.post('/s1pro/tickets/T1/status',json={'status':'随便写'}).status_code==422
