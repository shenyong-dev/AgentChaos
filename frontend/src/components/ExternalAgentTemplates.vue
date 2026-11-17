<script setup lang="ts">
import { computed, ref } from 'vue'
import { Download } from '@element-plus/icons-vue'
import caseTemplate from '../../../docs/design/external-agent/runtime/case.example.json?raw'
import registryTemplate from '../../../docs/design/external-agent/runtime/registry.example.json?raw'

const selected = ref<'case' | 'registry'>('case')
const example = JSON.parse(caseTemplate).case as {
  turns: { user: string }[]
  assertions: { contains: string[] }[]
}
const registry = JSON.parse(registryTemplate).targets[0] as {
  endpoint: string
  pinned_ip: string
  credential_env: string
}
const template = computed(() => selected.value === 'case'
  ? { filename: 'case.example.json', raw: caseTemplate }
  : { filename: 'registry.example.json', raw: registryTemplate })
const downloadUrl = computed(() => `data:application/json;charset=utf-8,${encodeURIComponent(template.value.raw)}`)
</script>

<template>
  <section class="templates" aria-label="接入模板">
    <h2>接入模板</h2>
    <div class="template-switch" role="group" aria-label="选择模板">
      <button type="button" :aria-pressed="selected === 'case'" @click="selected = 'case'">对话用例</button>
      <button type="button" :aria-pressed="selected === 'registry'" @click="selected = 'registry'">服务端登记</button>
    </div>

    <div v-if="selected === 'case'" class="preview">
      <div>
        <h3>发送给 Agent</h3>
        <ol><li v-for="turn in example.turns" :key="turn.user">{{ turn.user }}</li></ol>
      </div>
      <div>
        <h3>检查最后一条回答</h3>
        <p>应包含 <b v-for="text in example.assertions[0].contains" :key="text">「{{ text }}」</b></p>
      </div>
    </div>
    <dl v-else class="preview registry-preview">
      <div><dt>示例服务地址</dt><dd><code>{{ registry.endpoint }}</code></dd></div>
      <div><dt>固定 IP</dt><dd><code>{{ registry.pinned_ip }}</code></dd></div>
      <div><dt>凭据变量名</dt><dd><code>{{ registry.credential_env }}</code></dd></div>
    </dl>

    <div class="template-footer">
      <p>{{ selected === 'case' ? '下载后可修改消息与预期，再导入 JSON 草稿。' : '由维护者在服务端配置，凭据通过环境变量提供。' }}</p>
      <a :href="downloadUrl" :download="template.filename" class="download-link">
        <Download aria-hidden="true" />下载 JSON
      </a>
    </div>
    <details class="full-template">
      <summary>查看完整 JSON <code>{{ template.filename }}</code></summary>
      <pre tabindex="0" aria-label="完整模板 JSON"><code>{{ template.raw.trim() }}</code></pre>
    </details>
  </section>
</template>

<style scoped>
.templates { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--el-border-color-light); }
h2 { margin: 0 0 12px; font-size: 18px; }
.template-switch {
  display: inline-flex; flex-wrap: wrap; gap: 4px; padding: 4px;
  border: 1px solid var(--el-border-color-light); border-radius: 10px; background: var(--el-fill-color-light);
}
.template-switch button {
  padding: 8px 12px; border: 0; border-radius: 6px; background: transparent;
  color: var(--el-text-color-regular); font: inherit; font-size: 14px; cursor: pointer;
}
.template-switch button:hover { color: var(--el-color-primary); }
.template-switch button[aria-pressed="true"] {
  color: var(--el-color-primary); background: var(--el-bg-color); font-weight: 600;
  box-shadow: 0 1px 3px rgb(0 0 0 / 12%);
}
.preview {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px;
  margin: 14px 0 12px; padding: 16px; background: var(--el-fill-color-light); border-radius: 10px;
}
.registry-preview { grid-template-columns: 2fr 1fr 2fr; }
.preview h3, dt { margin: 0 0 7px; font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); }
.preview ol { margin: 0; padding-inline-start: 20px; }
.preview p, dd { margin: 0; }
.preview li, .preview p, dd { overflow-wrap: anywhere; line-height: 1.65; }
.template-footer { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px 16px; }
.template-footer p { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); }
.download-link {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 10px; border-radius: 7px;
  color: var(--el-color-primary); font-size: 14px; font-weight: 600; text-decoration: none;
}
.download-link:hover { background: var(--el-color-primary-light-9); }
.download-link svg { width: 16px; height: 16px; }
.full-template { margin-top: 12px; }
.full-template summary { padding-block: 7px; color: var(--el-text-color-primary); cursor: pointer; }
.full-template summary code { margin-inline-start: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
.full-template pre {
  margin: 6px 0 0; max-height: 320px; overflow: auto; padding: 14px;
  border: 1px solid var(--el-border-color-light); border-radius: 8px; background: var(--el-fill-color-light);
  white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.5;
}
:is(button, a, summary, pre):focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
@media (max-width: 640px) {
  .preview, .registry-preview { grid-template-columns: 1fr; gap: 12px; padding: 12px; }
}
</style>
