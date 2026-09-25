'use strict';
const $ = (id) => document.getElementById(id);
function addDetail(dl,label,value) {
  const dt=document.createElement('dt');dt.textContent=label;
  const dd=document.createElement('dd');dd.textContent=value??'暂无';
  dl.append(dt,dd);
}
async function loadOrder() {
  const orderNo=decodeURIComponent(location.pathname.split('/').filter(Boolean).at(-1)||'');
  try {
    const response=await fetch('/api/orders/'+encodeURIComponent(orderNo),{cache:'no-store'});
    if(response.status===404) { $('status').textContent='没有找到这笔演示订单。'; return; }
    if(!response.ok) throw new Error('订单暂时无法读取，请稍后重试。');
    const data=await response.json();
    if(!data?.found||data.source!=='demo'||!data.order||!Array.isArray(data.items)||!Array.isArray(data.events)) throw new Error('订单详情格式异常。');
    const order=data.order;
    $('order-number').textContent='订单号  '+order.order_no;
    $('shipping-status').textContent='当前配送状态：'+order.shipping_status;
    $('eta').textContent=order.estimated_delivery_at?`预计送达：${order.estimated_delivery_at}`:'预计送达：暂无预计送达时间';
    const items=$('items');
    for(const item of data.items) {
      const row=document.createElement('div');row.className='item-row';
      const name=document.createElement('strong');name.textContent=item.product_name;
      const detail=document.createElement('span');detail.textContent=`${item.product_line} · ${item.category} · 型号 ${item.model_code} · 数量 ${item.quantity}`;
      row.append(name,detail);items.append(row);
    }
    addDetail($('summary'),'支付状态',order.payment_status);
    addDetail($('summary'),'下单时间',order.placed_at);
    addDetail($('summary'),'购买渠道',order.purchase_channel);
    addDetail($('summary'),'实付金额',`${order.paid_amount} ${order.currency}`);
    for(const event of data.events) {
      const row=document.createElement('li');
      const time=document.createElement('time');time.textContent=event.occurred_at;
      const place=document.createElement('strong');place.textContent=event.location;
      const description=document.createElement('span');description.textContent=event.description;
      row.append(time,place,description);$('timeline').append(row);
    }
    $('status').hidden=true;$('content').hidden=false;document.title=`${order.order_no} · 订单详情`;
  } catch(error) {
    $('status').textContent=error instanceof TypeError?'订单服务暂时无法连接。':error.message;
  }
}
loadOrder();
