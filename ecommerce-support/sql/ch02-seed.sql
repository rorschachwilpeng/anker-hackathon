INSERT INTO faq(question,answer,category)
SELECT '退货政策是什么？','演示政策：签收后七天内，商品保持完好且符合退货条件可申请退货；具体以人工审核为准。','售后'
WHERE NOT EXISTS (SELECT 1 FROM faq WHERE question='退货政策是什么？');
INSERT INTO faq(question,answer,category)
SELECT '运费标准是什么？','演示政策：普通配送每单 8 元，订单实付满 99 元免基础运费。','配送'
WHERE NOT EXISTS (SELECT 1 FROM faq WHERE question='运费标准是什么？');
INSERT INTO conversations(user_id,status)
SELECT 'ch02-seed-demo','已转人工'
WHERE NOT EXISTS (SELECT 1 FROM conversations WHERE user_id='ch02-seed-demo');
INSERT INTO messages(conversation_id,role,content)
SELECT id,'user','演示消息：需要人工咨询' FROM conversations c WHERE user_id='ch02-seed-demo'
AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id);
INSERT INTO messages(conversation_id,role,content)
SELECT id,'assistant','演示消息：已登记人工咨询工单 TCH02DEMO001。' FROM conversations c WHERE user_id='ch02-seed-demo'
AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.role='assistant');
INSERT INTO tickets(ticket_no,conversation_id,description,ticket_type)
SELECT 'TCH02DEMO001',id,'演示工单：人工咨询','咨询' FROM conversations WHERE user_id='ch02-seed-demo'
AND NOT EXISTS (SELECT 1 FROM tickets WHERE ticket_no='TCH02DEMO001');
