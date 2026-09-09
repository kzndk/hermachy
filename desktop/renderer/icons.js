const paths = {
  panel:'M3 4h18v16H3ZM9 4v16', diff:'M4 3h16v18H4ZM8 8h8M8 15h8m-4-4v8', branch:'M6 3v12a4 4 0 0 0 4 4h2M6 7h8a4 4 0 0 0 4-4M4 3h4M16 3h4M12 17h4v4h-4Z', refresh:'M20 7a9 9 0 0 0-15-2L2 8m0-6v6h6m-4 9a9 9 0 0 0 15 2l3-3m0 6v-6h-6',
  plus:'M12 5v14M5 12h14', chat:'M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9H13a8.5 8.5 0 0 1 8 8v.5Z',
  arrow:'M12 19V5m-6 6 6-6 6 6', file:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h5',
  paperclip:'m21 11-8.5 8.5a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9 9a2 2 0 0 1-2.8-2.8L15 6',
  folder:'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
  search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0', settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',
  spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z', chevron:'m9 5 7 7-7 7', close:'M6 6l12 12M6 18 18 6',
  code:'m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16', check:'m5 12 4 4L19 6', stop:'M6 6h12v12H6Z',
  leaf:'M20 4C9 2 2 7 5 16c9 4 16-1 15-12ZM4 21 15 10', link:'M10 13a5 5 0 0 0 7 .2l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7-.2l-3 3a5 5 0 0 0 7 7l2-2',
  menu:'M4 6h16M4 12h16M4 18h16', copy:'M9 9h12v12H9ZM5 15H3V3h12v2', clock:'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
};
export const icon = (name, cls='') => `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.spark}"/></svg>`;
