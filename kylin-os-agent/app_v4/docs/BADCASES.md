# Badcase 记录

## 已修复

### B06: 匿名请求共用 thread_id="default"
- **现象**：不传 conversation_id 时所有匿名用户共享状态
- **根因**：`runner.py` 里写死了 `thread_id = conversation_id or "default"`
- **修复**：改为自动生成 `uuid.uuid4()`

### B09: 工具输出扫描结果没影响后续行为
- **现象**：`scan_untrusted_output` 返回结果但 execute_node 没用它
- **根因**：app_v2 里 scan 结果只存着，不走不同路径
- **修复**：app_v4 把 scan 结果注入 `output_scan` 传给 summarize_node 的 LLM 上下文

### B10: system_logs 返回模拟数据标记成功
- **现象**：Windows 无 journalctl 时返回 mock rows + status=ok
- **修复**：返回 `status="unavailable"` + 结构化错误信息

### 高危识别失效（新发现）
- **现象**：`rm -rf /` 等高危命令没被拦截
- **根因**：`_normalize` 的 `zero_width` 字符串包含普通连字符 `-`，导致 regex 文本中所有连字符被删除
- **修复**：移除 `zero_width` 中的连字符字符

## 待观察

### Fake model 意图识别有限
- **现象**：假模型只做关键词匹配，不能真正理解中文意图
- **影响**：只用于测试，真实场景需要真实 LLM
- **缓解**：通过 `APP_V4_USE_FAKE_MODEL` 切换
