INSERT INTO demo_orders(order_no,placed_at,purchase_channel,payment_status,paid_amount,currency,shipping_status,estimated_delivery_at,source)
SELECT 'DEMO-1001','2026-09-24 10:00:00','演示商城','已支付',2999.00,'CNY','运输中',NULL,'demo'
WHERE NOT EXISTS (SELECT 1 FROM demo_orders WHERE order_no='DEMO-1001');

INSERT INTO demo_order_items(order_no,product_name,product_line,category,model_code,quantity)
SELECT 'DEMO-1001','eufy Robot Vacuum Omni S1 Pro','智能家居','扫地机器人','T2080',1
WHERE NOT EXISTS (SELECT 1 FROM demo_order_items WHERE order_no='DEMO-1001');

INSERT INTO demo_order_events(order_no,occurred_at,location,description)
SELECT 'DEMO-1001','2026-09-24 16:20:00','演示揽收点','已揽收'
WHERE NOT EXISTS (SELECT 1 FROM demo_order_events WHERE order_no='DEMO-1001' AND occurred_at='2026-09-24 16:20:00');

INSERT INTO demo_order_events(order_no,occurred_at,location,description)
SELECT 'DEMO-1001','2026-09-25 08:30:00','演示分拨中心','运输中'
WHERE NOT EXISTS (SELECT 1 FROM demo_order_events WHERE order_no='DEMO-1001' AND occurred_at='2026-09-25 08:30:00');
