/* Fictional fixtures for UI exploration. No system reads, writes or network requests. */
var extraDemoApps = [
  {
    name: 'Node.js', icon: 'N', iconBg: '#edf2e8', iconFg: '#587b46', active: '团队默认', default: '团队默认',
    description: '体验三个方案、多个候选值和已保存值；NODE_OPTIONS 包含分号，但按整体字符串编辑。',
    profiles: [
      {name:'团队默认', vars:[
        ['NODE_HOME','User','replace','C:\\Dev Tools\\Node22', {savedValue:'D:\\Portable Apps\\Node22',choices:['C:\\Dev Tools\\Node22','D:\\便携工具\\Node22']}],
        ['Path','User','+append','%NODE_HOME%\\bin'],
        ['NODE_OPTIONS','User','replace','--max-old-space-size=4096 --require "C:\\团队工具\\初始化;本地.js"',{special:true}]
      ]},
      {name:'实验版本',vars:[
        ['NODE_HOME','User','replace','C:\\Dev Tools\\Node24',{choices:['C:\\Dev Tools\\Node24','D:\\实验工具\\Node24']}],
        ['Path','User','-append','%NODE_HOME%\\bin'],
        ['NODE_OPTIONS','User','replace','--max-old-space-size=8192',{special:true}]
      ]},
      {name:'项目本地',vars:[
        ['NODE_HOME','User','replace','D:\\演示项目\\前端工作区\\.tools\\node'],
        ['Path','User','+append','%NODE_HOME%\\bin;D:\\演示项目\\前端工作区\\node_modules\\.bin'],
        ['NODE_OPTIONS','User','replace','',{special:true}]
      ]}
    ]
  },
  {
    name: '.NET SDK',icon: 'D',iconBg:'#eeebf4',iconFg:'#6b5d8c',active:null,default:'日常开发',
    description:'默认方案部分匹配。切换“构建服务器”查看系统作用域；模拟应用前可审阅作用域和目标值。',
    profiles:[
      {name:'日常开发',vars:[
        ['DOTNET_ROOT','User','replace','C:\\Dev Tools\\dotnet',{savedValue:'D:\\Portable Apps\\dotnet',choices:['C:\\Dev Tools\\dotnet','D:\\Portable Apps\\dotnet']}],
        ['Path','User','-append','%DOTNET_ROOT%'],
        ['DOTNET_CLI_TELEMETRY_OPTOUT','User','replace','1']
      ]},
      {name:'构建服务器',vars:[
        ['DOTNET_ROOT','System','replace','C:\\Build Tools\\dotnet'],
        ['Path','System','+append','%DOTNET_ROOT%'],
        ['DOTNET_CLI_TELEMETRY_OPTOUT','System','replace','1']
      ]}
    ]
  },
  {
    name:'CUDA Toolkit',icon:'C',iconBg:'#eef1e7',iconFg:'#657c38',active:'兼容工具链',default:'GPU 开发',
    description:'准备方案与最近应用记录不同。系统 Path 包含重复/空元素，可双击核对和排序；所有值都是演示数据。',
    profiles:[
      {name:'GPU 开发',vars:[
        ['CUDA_PATH','System','replace','C:\\Program Files\\GPU Toolkit\\CUDA\\v12.4'],
        ['Path','System','+append','%CUDA_PATH%\\bin;%CUDA_PATH%\\libnvvp'],
        ['CUDA_CACHE_PATH','User','replace','D:\\开发缓存\\CUDA']
      ]},
      {name:'兼容工具链',vars:[
        ['CUDA_PATH','System','replace','C:\\Program Files\\GPU Toolkit\\CUDA\\v11.8'],
        ['Path','System','-append','%CUDA_PATH%\\bin'],
        ['CUDA_CACHE_PATH','User','replace','C:\\Demo Cache\\CUDA']
      ]},
      {name:'仅运行库（不调整 Path）',vars:[
        ['CUDA_PATH','System','replace','D:\\Portable Apps\\CUDA Runtime']
      ]}
    ]
  },
  {
    name:'中文长路径工具链',icon:'工',iconBg:'#e9f0f2',iconFg:'#507484',active:null,default:'团队共享目录（中文及空格）',
    description:'查看长路径、中文和引号的可读性。配置编辑器有多条变量，可体验表格滚动、属性修改与方案归属。',
    profiles:[
      {name:'团队共享目录（中文及空格）',vars:[
        ['TEAM_TOOLS_HOME','User','replace','D:\\团队共享资料\\研发工程项目\\多语言构建工具链\\Release Candidates\\2026 示例版本\\工具与依赖\\便携工具集',{savedValue:' D:\\团队工具\\稳定版 ',choices:['D:\\团队工具\\稳定版','E:\\备用工具\\中文 工具目录']}],
        ['Path','User','+append','%TEAM_TOOLS_HOME%\\bin'],
        ['TEAM_BUILD_CACHE_DIRECTORY','User','replace','D:\\团队共享资料\\构建缓存\\项目 Alpha 与 Beta\\本地缓存目录'],
        ['TEAM_LOG_LEVEL','User','replace','INFO',{choices:['DEBUG','TRACE']}],
        ['TEAM_BUILD_FLAGS','User','replace','--output "D:\\构建结果\\项目 Alpha" --define "label=演示;渠道=本地"',{special:true}],
        ['TEAM_DOWNLOAD_MIRROR','User','replace','https://packages.example.invalid/team']
      ]},
      {name:'隔离项目工作区 · 不与团队目录共用工具',vars:[
        ['TEAM_TOOLS_HOME','User','replace','E:\\演示工作区\\项目独立工具'],
        ['Path','User','-append','%TEAM_TOOLS_HOME%\\bin'],
        ['TEAM_BUILD_CACHE_DIRECTORY','User','replace','E:\\演示工作区\\项目独立缓存'],
        ['TEAM_LOG_LEVEL','User','replace','DEBUG'],
        ['TEAM_BUILD_FLAGS','User','replace','--offline --output "E:\\演示工作区\\输出"',{special:true}],
        ['TEAM_DOWNLOAD_MIRROR','User','replace','']
      ]}
    ]
  },
  {
    name:'列表与空值',icon:'列',iconBg:'#f0eeea',iconFg:'#7a705d',active:null,default:'重复元素与空值',
    description:'双击 DEMO_SEARCH_PATH 查看重复和空元素；比较空字符串与不存在变量。自定义排序方案保留原型限制，预览不会假装执行。',
    profiles:[
      {name:'重复元素与空值',vars:[
        ['DEMO_SEARCH_PATH','User','+append','C:\\Demo\\Alpha;;C:\\Demo\\Alpha; C:\\Demo\\Beta ;'],
        ['DEMO_EMPTY_VALUE','User','replace',''],
        ['DEMO_UNSET_VALUE','User','replace',''],
        ['DEMO_COMMAND','User','replace','cmd /c "echo alpha;beta"',{special:true}]
      ]},
      {name:'自定义排序预览',vars:[
        ['DEMO_SEARCH_PATH','User','append','C:\\Demo\\Beta;C:\\Demo\\Alpha'],
        ['DEMO_COMMAND','User','replace','cmd /c "echo reordered;demo"',{special:true}]
      ]}
    ]
  },
  {
    name:'共享变量冲突示例',icon:'共',iconBg:'#f3ece5',iconFg:'#966f45',active:null,default:'另一组运行时',
    description:'同时勾选此应用与 Node.js，再预览：两个方案设置不同 NODE_HOME，展示冲突及禁止确认的状态。',
    profiles:[{name:'另一组运行时',vars:[
      ['NODE_HOME','User','replace','D:\\Shared Runtime\\Node'],
      ['Path','User','-append','%NODE_HOME%\\bin']
    ]}]
  }
];

var extraDemoWindows = {
  'User|NODE_HOME':'C:\\Dev Tools\\Node22',
  'User|NODE_OPTIONS':'--max-old-space-size=4096 --require "C:\\团队工具\\初始化;本地.js"',
  'User|DOTNET_ROOT':'D:\\Portable Apps\\dotnet',
  'User|DOTNET_CLI_TELEMETRY_OPTOUT':'1',
  'System|DOTNET_ROOT':'C:\\Build Tools\\dotnet',
  'System|DOTNET_CLI_TELEMETRY_OPTOUT':'1',
  'System|CUDA_PATH':'C:\\Program Files\\GPU Toolkit\\CUDA\\v11.8',
  'System|PATH':'C:\\Windows\\System32;;%CUDA_PATH%\\bin;%CUDA_PATH%\\libnvvp;C:\\Windows\\System32;%DOTNET_ROOT%;',
  'User|CUDA_CACHE_PATH':'C:\\Demo Cache\\CUDA',
  'User|TEAM_TOOLS_HOME':'D:\\团队工具\\稳定版',
  'User|TEAM_LOG_LEVEL':'INFO',
  'User|DEMO_SEARCH_PATH':'C:\\Demo\\Alpha;;C:\\Demo\\Alpha;C:\\Demo\\Gamma;',
  'User|DEMO_EMPTY_VALUE':'',
  'User|DEMO_COMMAND':'cmd /c "echo alpha;beta"'
};
var extraDemoUserPath = ['%NODE_HOME%\\bin','%DOTNET_ROOT%','%TEAM_TOOLS_HOME%\\bin'];
