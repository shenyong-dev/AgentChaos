<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElButton, ElDialog } from 'element-plus'

const props = withDefaults(defineProps<{
  label: string; left: unknown; right: unknown
  leftRun: string; rightRun: string; leftTitle?: string; rightTitle?: string
}>(), { leftTitle: '左侧运行', rightTitle: '右侧运行' })
const open = ref(false)
const format = (value: unknown) => value === undefined ? '未记录' : JSON.stringify(value, null, 2)
watch(() => [props.label, props.leftRun, props.rightRun, props.left, props.right], () => { open.value = false })
</script>

<template>
  <ElButton link type="primary" :aria-label="`查看${label}实际值`" @click="open = true">查看实际值</ElButton>
  <ElDialog v-model="open" :title="`${label} · 实际值`" class="execution-control-dialog comparison-values-dialog" width="min(1040px,94vw)" append-to-body align-center destroy-on-close>
    <div class="comparison-value-columns">
      <section v-for="side in (['left', 'right'] as const)" :key="side" :aria-label="side === 'left' ? leftTitle : rightTitle">
        <header><h3>{{ side === 'left' ? leftTitle : rightTitle }}</h3><span>{{ side === 'left' ? leftRun : rightRun }}</span></header>
        <pre tabindex="0" :aria-label="`${side === 'left' ? leftTitle : rightTitle}实际值`">{{ format(side === 'left' ? left : right) }}</pre>
      </section>
    </div>
    <template #footer><ElButton @click="open = false">关闭</ElButton></template>
  </ElDialog>
</template>

<style scoped>
.comparison-value-columns { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 18px; }
.comparison-value-columns section { min-width: 0; border: 1px solid var(--ac-border); border-radius: 10px; overflow: hidden; }
.comparison-value-columns header { padding: 16px; border-bottom: 1px solid var(--ac-border); }
.comparison-value-columns h3 { margin: 0 0 6px; font-size: 14px; color: var(--ac-ink); }
.comparison-value-columns span { font: 12px/1.6 Consolas,monospace; color: var(--ac-muted); overflow-wrap: anywhere; }
.comparison-value-columns pre { margin: 0; padding: 16px; max-height: 52dvh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ac-ink); background: var(--ac-surface-soft); font: 12px/1.7 Consolas,monospace; overscroll-behavior: contain; }
.comparison-value-columns pre:focus-visible { outline: 2px solid var(--ac-accent); outline-offset: -2px; }
@media(max-width:700px) { .comparison-value-columns { grid-template-columns: 1fr; gap: 14px; }.comparison-value-columns pre { max-height: 35dvh; } }
</style>
