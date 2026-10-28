<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="AgentChaos 首页">
        <span class="brand-symbol" aria-hidden="true">a<span>c</span></span>
        <span>AgentChaos<small>智能体可靠性实验室</small></span>
      </a>
      <div class="workspace-label">可靠性工作区</div>
      <nav aria-label="主导航">
        <RouterLink to="/environment" class="nav-link">
          <span class="nav-icon" aria-hidden="true">◫</span>工程环境
        </RouterLink>
        <RouterLink to="/runs" class="nav-link">
          <span class="nav-icon" aria-hidden="true">≋</span>运行证据
        </RouterLink>
      </nav>
      <div class="scope-note">
        <span class="eyebrow">证据与规则</span>
        <p>看见实际发生的事。</p>
        <small>对照工具请求、业务事实与独立预期，复核每一条判定。</small>
      </div>
      <div class="sidebar-footer">本地开发环境<small>AgentChaos</small></div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><span>AgentChaos <i>/</i> {{ $route.path.startsWith('/runs') ? '运行证据' : '工程环境' }}</span><span class="phase-label">可靠性评估</span></header>
      <main><RouterView /></main>
    </div>
  </div>
</template>
