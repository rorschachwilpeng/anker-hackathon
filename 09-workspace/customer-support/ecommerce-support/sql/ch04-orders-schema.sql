-- ch04 · 固定订单演示数据与会话内订单上下文
CREATE TABLE demo_orders (
  order_no VARCHAR(64) NOT NULL,
  placed_at DATETIME NOT NULL,
  purchase_channel VARCHAR(64) NOT NULL,
  payment_status VARCHAR(32) NOT NULL,
  paid_amount DECIMAL(10,2) NOT NULL,
  currency VARCHAR(3) NOT NULL,
  shipping_status VARCHAR(32) NOT NULL,
  estimated_delivery_at DATETIME NULL,
  source VARCHAR(16) NOT NULL DEFAULT 'demo',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='只读演示订单';

CREATE TABLE demo_order_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_no VARCHAR(64) NOT NULL,
  product_name VARCHAR(128) NOT NULL,
  product_line VARCHAR(64) NOT NULL,
  category VARCHAR(64) NOT NULL,
  model_code VARCHAR(32) NOT NULL,
  quantity INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_demo_order_items_order (order_no),
  CONSTRAINT fk_demo_order_items_order FOREIGN KEY (order_no) REFERENCES demo_orders (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='演示订单商品明细';

CREATE TABLE demo_order_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_no VARCHAR(64) NOT NULL,
  occurred_at DATETIME NOT NULL,
  location VARCHAR(128) NOT NULL,
  description VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_demo_order_event (order_no, occurred_at),
  CONSTRAINT fk_demo_order_events_order FOREIGN KEY (order_no) REFERENCES demo_orders (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='演示订单物流事件';

CREATE TABLE conversation_order_context (
  conversation_id BIGINT UNSIGNED NOT NULL,
  order_no VARCHAR(64) NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (conversation_id),
  CONSTRAINT fk_order_context_conversation FOREIGN KEY (conversation_id) REFERENCES conversations (id),
  CONSTRAINT fk_order_context_order FOREIGN KEY (order_no) REFERENCES demo_orders (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话当前已查询订单';
