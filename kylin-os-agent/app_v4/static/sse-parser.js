/*!
 * sse-parser.js — 无依赖的 SSE 协议解析纯函数。
 *
 * 设计目标：
 *   - 纯函数（无 DOM / 无副作用 / 无状态），可在浏览器与 Node 中同源码运行，
 *     从而用 node:test 做单元测试，覆盖 SSE 协议的全部边界。
 *   - 正确处理 SSE 协议的三种换行（\r\n、\r、\n）、跨 chunk 不完整事件、
 *     decoder.flush 残留、多 data 行拼接、JSON 解析失败。
 *
 * 协议要点（SSE spec）：
 *   - 事件之间以空行（"\n\n"）分隔。
 *   - 一个事件可含多行 "data:"，值按 "\n" 拼接后作为整体解析。
 *   - 行尾可以是 \r\n、\r 或 \n；解析前统一归一化为 \n。
 *
 * 用法（浏览器）：
 *   <script src="sse-parser.js"></script>
 *   var r = SSEParser.parseSSEChunk(buffer, chunk);
 *   // r.events: 已解析事件数组；r.buffer: 未完成的残留，下次传入。
 *   // 流结束时调用 SSEParser.parseSSEFlush(buffer) 冲刷尾部事件。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SSEParser = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var DATA_PREFIX = 'data:';
  var EVENT_PREFIX = 'event:';

  /**
   * 解析一个新到达的 SSE chunk。
   *
   * @param {string} buffer — 上次调用残留的、尚未构成完整事件的尾部。
   * @param {string} chunk  — 新到达的字节解码后的字符串。
   * @returns {{events: Array, buffer: string}}
   *   events: 本次 chunk 中完整解析出的事件对象数组（已 JSON.parse）。
   *   buffer: 仍未构成完整事件的尾部，应由调用方保存并下次传入。
   */
  function parseSSEChunk(buffer, chunk) {
    var buf = (buffer || '') + (chunk == null ? '' : String(chunk));

    // 归一化换行：\r\n -> \n，然后剩余 \r -> \n。覆盖 SSE 协议允许的全部三种换行。
    buf = buf.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    // 以空行切分事件块。除最后一块外，其余都是完整事件。
    var blocks = buf.split('\n\n');
    var remaining = blocks.pop();
    if (remaining === undefined) remaining = '';

    var events = [];
    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];
      if (!block) continue;

      var dataLines = [];
      var lines = block.split('\n');
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        if (line.indexOf(DATA_PREFIX) === 0) {
          var v = line.slice(DATA_PREFIX.length);
          if (v.charAt(0) === ' ') v = v.slice(1); // 去掉 ":" 后可有可无的空格
          dataLines.push(v);
        }
        // "event:" / "id:" / 注释行(":") / 空行：此处忽略，事件类型由 JSON 内 event 字段承载。
      }

      if (dataLines.length === 0) continue; // 无数据的事件（如心跳）跳过

      var dataStr = dataLines.join('\n'); // 多 data 行按 \n 拼接
      try {
        events.push(JSON.parse(dataStr));
      } catch (e) {
        // 解析失败不抛错、不中断流；产出 error 事件让上层决定如何展示。
        events.push({ event: 'error', reason: 'json_parse', raw: dataStr.slice(0, 200) });
      }
    }

    return { events: events, buffer: remaining };
  }

  /**
   * 流结束时的尾部冲刷。
   *
   * SSE 规范要求事件以空行结尾，但部分实现可能省略最后的 \n\n。
   * 调用方在流结束时传入最终 buffer，本函数追加 "\n\n" 尝试解析残留。
   *
   * @param {string} buffer — 流结束时仍未解析的尾部。
   * @returns {Array} 尾部中解析出的事件数组（通常 0 或 1 个）。
   */
  function parseSSEFlush(buffer) {
    if (!buffer || !buffer.trim()) return [];
    var r = parseSSEChunk(buffer, '\n\n');
    return r.events;
  }

  return {
    parseSSEChunk: parseSSEChunk,
    parseSSEFlush: parseSSEFlush,
  };
});
