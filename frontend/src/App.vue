<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="AgentChaos 首页">
        <span class="brand-symbol" aria-hidden="true">a<span>c</span></span>
        <span>AgentChaos<small>智能体可靠性实验室</small></span>
      </a>
      <div class="workspace-label">项目空间 <span>01</span></div>
      <nav aria-label="主导航">
        <RouterLink to="/environment" class="nav-link">
          <span class="nav-icon" aria-hidden="true">◫</span>工程环境<span class="nav-key">T01</span>
        </RouterLink>
      </nav>
      <div class="scope-note">
        <span class="eyebrow">当前建设范围</span>
        <p>先让每一层连接可验证。</p>
        <small>测试工作台、用例与证据报告将随获准任务逐项接入。</small>
      </div>
      <div class="sidebar-footer"><span class="small-dot"></span>本地开发环境<small>v0.1.0</small></div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><span>AgentChaos <i>/</i> 工程环境</span><span class="phase-label">基础建设阶段</span></header>
      <main><RouterView /></main>
    </div>
  </div>
</template>
