'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseSSEChunk, parseSSEFlush } = require('../static/sse-parser.js');

function data(obj) {
  return 'data: ' + JSON.stringify(obj);
}

test('解析单个 token 事件', () => {
  const r = parseSSEChunk('', data({ event: 'token', delta: '哈' }) + '\n\n');
  assert.equal(r.events.length, 1);
  assert.equal(r.events[0].event, 'token');
  assert.equal(r.events[0].delta, '哈');
  assert.equal(r.buffer, '');
});

test('同 chunk 内多个事件（token 序列）', () => {
  const payload =
    data({ event: 'token', delta: 'A' }) + '\n\n' +
    data({ event: 'token', delta: 'B' }) + '\n\n' +
    data({ event: 'token', delta: 'C' }) + '\n\n';
  const r = parseSSEChunk('', payload);
  assert.deepEqual(r.events.map((e) => e.delta), ['A', 'B', 'C']);
});

test('跨 chunk 的不完整事件：data 行在 chunk 边界被截断', () => {
  const full = data({ event: 'token', delta: 'Hello' }) + '\n\n';
  // 第一个 chunk 在 "Hell" 之后截断，"\n\n" 与 "o" 都在第二个 chunk
  const mid = full.indexOf('Hell') + 4;
  const r1 = parseSSEChunk('', full.slice(0, mid));
  assert.equal(r1.events.length, 0, '截断时不应产出事件');
  assert.ok(r1.buffer.length > 0, '应保留残留');

  const r2 = parseSSEChunk(r1.buffer, full.slice(mid));
  assert.equal(r2.events.length, 1);
  assert.equal(r2.events[0].delta, 'Hello');
});

test('跨 chunk：分隔符 \n\n 被截断', () => {
  const r1 = parseSSEChunk('', 'data: {"event":"token","delta":"X"}\n');
  assert.equal(r1.events.length, 0);
  const r2 = parseSSEChunk(r1.buffer, '\n');
  assert.equal(r2.events.length, 1);
  assert.equal(r2.events[0].delta, 'X');
});

test('CRLF 换行（\\r\\n）应正确解析', () => {
  const r = parseSSEChunk('', data({ event: 'done', answer: 'ok' }) + '\r\n\r\n');
  assert.equal(r.events.length, 1);
  assert.equal(r.events[0].event, 'done');
  assert.equal(r.events[0].answer, 'ok');
});

test('旧式 \\r 换行应正确解析', () => {
  const r = parseSSEChunk('', data({ event: 'token', delta: 'r' }) + '\r\r');
  assert.equal(r.events.length, 1);
  assert.equal(r.events[0].delta, 'r');
});

test('多 data 行按 \\n 拼接（服务端美化 JSON 分行发送）', () => {
  // 真实场景：服务端对 JSON 做 pretty-print 后，一个事件可能跨多行 data: 发送。
  // 客户端应把这些行按 \n 拼接后解析为完整 JSON。
  const obj = { event: 'token', delta: 'AB' };
  const pretty = JSON.stringify(obj, null, 2); // 含 \n 的美化 JSON
  const multiLine = pretty
    .split('\n')
    .map((line) => 'data: ' + line)
    .join('\n') + '\n\n';
  const r = parseSSEChunk('', multiLine);
  assert.equal(r.events.length, 1);
  assert.equal(r.events[0].event, 'token');
  assert.equal(r.events[0].delta, 'AB');
});

test('done 事件作为最终事件解析', () => {
  const payload =
    data({ event: 'token', delta: 'x' }) + '\n\n' +
    data({ event: 'done', answer: '最终回答', stream_stats: { ttft_ms: 1, total_ms: 2, token_count: 1 } }) + '\n\n';
  const r = parseSSEChunk('', payload);
  assert.equal(r.events.length, 2);
  const done = r.events[1];
  assert.equal(done.event, 'done');
  assert.equal(done.answer, '最终回答');
  assert.equal(done.stream_stats.token_count, 1);
});

test('done 兜底：流结束时尾部 data 无结尾 \n\n，flush 仍能解析', () => {
  // 模拟 decoder.flush 后残留一个没有尾部空行的 done 事件
  const tail = data({ event: 'done', answer: '兜底回答' });
  const flushed = parseSSEFlush(tail);
  assert.equal(flushed.length, 1);
  assert.equal(flushed[0].event, 'done');
  assert.equal(flushed[0].answer, '兜底回答');
});

test('flush 空残留不产出事件', () => {
  assert.equal(parseSSEFlush('').length, 0);
  assert.equal(parseSSEFlush('   ').length, 0);
});

test('JSON 解析失败产出 error 事件且不中断后续', () => {
  const payload =
    'data: {不是合法 json\n\n' +
    data({ event: 'token', delta: 'ok' }) + '\n\n';
  const r = parseSSEChunk('', payload);
  assert.equal(r.events.length, 2);
  assert.equal(r.events[0].event, 'error');
  assert.equal(r.events[0].reason, 'json_parse');
  assert.equal(r.events[1].event, 'token');
  assert.equal(r.events[1].delta, 'ok');
});

test('残留 buffer 跨多次 chunk 逐步拼接最终解析', () => {
  const full = data({ event: 'token', delta: '结束' }) + '\n\n';
  let buf = '';
  const allEvents = [];
  for (let i = 0; i < full.length; i += 3) {
    const piece = full.slice(i, i + 3);
    const r = parseSSEChunk(buf, piece);
    allEvents.push(...r.events);
    buf = r.buffer;
  }
  assert.ok(allEvents.length >= 1, '最终应解析出事件');
  assert.equal(allEvents[allEvents.length - 1].delta, '结束');
});

test('data: 后可有可无的空格都兼容', () => {
  const withSpace = parseSSEChunk('', 'data: {"event":"token","delta":"a"}\n\n');
  const noSpace = parseSSEChunk('', 'data:{"event":"token","delta":"b"}\n\n');
  assert.equal(withSpace.events[0].delta, 'a');
  assert.equal(noSpace.events[0].delta, 'b');
});
