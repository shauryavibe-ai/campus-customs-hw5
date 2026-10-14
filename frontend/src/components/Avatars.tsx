// The warehouse employees, one per agent, drawn in SVG (no image files).
// Arms, legs and props are separate groups so CSS can animate them:
//   idle    - breathing
//   working - each role's own action (carry a box, tap a calculator, turn a wrench, talk, point)
//   dance   - celebration: jumping, waving, each with its own timing

import type { ReactNode } from 'react'

export type Role = 'boss' | 'inventory' | 'accounting' | 'facilities' | 'customer_service'
export type AvatarState = 'idle' | 'working' | 'done' | 'blocked' | 'dance'

interface Look {
  skin: string
  hair: string
  shirt: string        // torso
  sleeve: string
  legs: string
  shoes: string
}

const LOOKS: Record<Role, Look> = {
  boss: { skin: '#f1c7a6', hair: '#3b2a20', shirt: '#0f2347', sleeve: '#0f2347', legs: '#14254a', shoes: '#0a0f1c' },
  accounting: { skin: '#c68e63', hair: '#1c1714', shirt: '#f4f6fb', sleeve: '#f4f6fb', legs: '#2b3445', shoes: '#151a24' },
  inventory: { skin: '#8d5a3b', hair: '#1a1210', shirt: '#7c8799', sleeve: '#7c8799', legs: '#3b5b8c', shoes: '#5a3a22' },
  facilities: { skin: '#e0ac85', hair: '#7a4a24', shirt: '#2f5fa8', sleeve: '#2f5fa8', legs: '#2f5fa8', shoes: '#1d2433' },
  customer_service: { skin: '#f3d2b5', hair: '#b5652b', shirt: '#4f8fe0', sleeve: '#4f8fe0', legs: '#26344d', shoes: '#151a24' },
}

const LABEL: Record<Role, string> = {
  boss: 'Boss in a suit and Yale-blue tie', accounting: 'Accountant in shirt, trousers and tie with a calculator',
  inventory: 'Inventory worker in hard hat and hi-vis vest', facilities: 'Facilities technician in coveralls with a wrench',
  customer_service: 'Customer service agent with a headset',
}

function Outfit({ role }: { role: Role }): ReactNode {
  switch (role) {
    case 'boss':
      return <>
        <path d="M27 37 L32 46 L37 37 Z" fill="#f4f6fb" />                          {/* shirt V */}
        <path d="M31 39 L33 39 L34.5 52 L32 55 L29.5 52 Z" fill="#00356b" stroke="#6fa8ff" strokeWidth=".6" />  {/* tie */}
        <path d="M22 37 L29 37 L32 50 L27 64 Z M42 37 L35 37 L32 50 L37 64 Z" fill="#183268" opacity=".9" /> {/* lapels */}
        <rect x="38" y="44" width="2" height="5" rx="1" fill="#f4f6fb" />            {/* pocket square */}
      </>
    case 'accounting':
      return <>
        <path d="M27 36 L32 41 L37 36" fill="none" stroke="#c9d3e3" strokeWidth="1.4" />   {/* collar */}
        <path d="M31 39 L33 39 L34.5 54 L32 57 L29.5 54 Z" fill="#286dc0" />          {/* tie */}
        <rect x="21" y="62" width="22" height="3" fill="#151a24" />                     {/* belt */}
        <rect x="35.5" y="42" width="5" height="6" rx="1" fill="none" stroke="#c9d3e3" strokeWidth=".8" /> {/* pocket */}
        <rect x="37" y="40" width="1.2" height="6" fill="#00356b" />                    {/* pen */}
      </>
    case 'inventory':
      return <>
        <path d="M21 37 L28 37 L28 64 L21 64 Z M36 37 L43 37 L43 64 L36 64 Z" fill="#ff8a2b" />  {/* hi-vis vest */}
        <rect x="21" y="50" width="7" height="2.5" fill="#e8eef8" /><rect x="36" y="50" width="7" height="2.5" fill="#e8eef8" />
        <rect x="21" y="56" width="7" height="2.5" fill="#e8eef8" /><rect x="36" y="56" width="7" height="2.5" fill="#e8eef8" />
      </>
    case 'facilities':
      return <>
        <rect x="27" y="40" width="10" height="9" rx="1" fill="#244c88" />               {/* bib pocket */}
        <rect x="20" y="59" width="24" height="5" fill="#6b4423" />                      {/* tool belt */}
        <rect x="23" y="60" width="3" height="7" fill="#c9d3e3" /><rect x="38" y="60" width="2.5" height="8" fill="#ffcc33" />
      </>
    case 'customer_service':
      return <>
        <path d="M24 37 L29 37 L31 64 L23 64 Z M40 37 L35 37 L33 64 L41 64 Z" fill="#2f6fd0" />  {/* cardigan */}
        <path d="M28 37 L32 42 L36 37" fill="none" stroke="#f4f6fb" strokeWidth="1.6" />  {/* polo collar */}
        <rect x="34" y="44" width="6" height="4" rx="1" fill="#f4f6fb" /><rect x="35" y="45.3" width="4" height="1" fill="#00356b" /> {/* badge */}
      </>
  }
}

function Headwear({ role, look }: { role: Role; look: Look }): ReactNode {
  switch (role) {
    case 'boss':
      return <path d="M20 19 C21 6 43 6 44 19 C40 13 26 12 20 19 Z" fill={look.hair} />
    case 'accounting':
      return <>
        <path d="M20 18 C21 7 43 7 44 18 C38 12 27 12 20 18 Z" fill={look.hair} />
        <circle cx="27.5" cy="22" r="3.6" fill="none" stroke="#1c1714" strokeWidth="1.2" />
        <circle cx="36.5" cy="22" r="3.6" fill="none" stroke="#1c1714" strokeWidth="1.2" />
        <line x1="31" y1="22" x2="33" y2="22" stroke="#1c1714" strokeWidth="1.2" />
        <line x1="44" y1="15" x2="47" y2="9" stroke="#00356b" strokeWidth="1.6" strokeLinecap="round" />  {/* pen behind ear */}
      </>
    case 'inventory':
      return <>
        <path d="M18 17 C19 4 45 4 46 17 Z" fill="#ffcc33" stroke="#e0a800" strokeWidth="1" />  {/* hard hat */}
        <rect x="15" y="15.5" width="34" height="3.4" rx="1.7" fill="#ffcc33" stroke="#e0a800" strokeWidth=".8" />
        <rect x="30.5" y="6" width="3" height="10" rx="1" fill="#e0a800" />
      </>
    case 'facilities':
      return <>
        <path d="M20 17 C21 7 43 7 44 17 Z" fill="#00356b" />                             {/* work cap */}
        <rect x="34" y="14.5" width="15" height="3.4" rx="1.7" fill="#00356b" />
        <text x="31" y="15" textAnchor="middle" fontSize="5" fontWeight="900" fill="#ffffff" fontFamily="system-ui">CC</text>
      </>
    case 'customer_service':
      return <>
        <path d="M19 20 C19 3 45 3 45 20 C45 26 46 30 44 33 C42 22 22 22 20 33 C18 30 19 26 19 20 Z" fill={look.hair} />
        <path d="M18 23 C18 6 46 6 46 23" fill="none" stroke="#1d2433" strokeWidth="2.2" />  {/* headset band */}
        <rect x="15.5" y="20" width="5" height="8" rx="2.5" fill="#1d2433" />
        <path className="mic" d="M18 27 Q20 33 27 31" fill="none" stroke="#1d2433" strokeWidth="1.4" />
        <circle className="mic" cx="27.5" cy="31" r="1.6" fill="#6fa8ff" />
      </>
  }
}

function Prop({ role }: { role: Role }): ReactNode {
  switch (role) {
    case 'boss':         // clipboard
      return <g className="prop"><rect x="44" y="52" width="10" height="13" rx="1.5" fill="#c79a5b" /><rect x="45.5" y="54.5" width="7" height="9" fill="#f4f6fb" />
        <rect x="47" y="51" width="4" height="2.5" rx="1" fill="#9aa6bb" /></g>
    case 'accounting':   // calculator
      return <g className="prop"><rect x="44" y="52" width="10" height="13" rx="1.5" fill="#2b3445" /><rect x="45.5" y="53.5" width="7" height="3" fill="#a8f0c8" />
        {[0, 1, 2].map(r => [0, 1, 2].map(c => <rect key={`${r}${c}`} x={45.6 + c * 2.4} y={58 + r * 2.2} width="1.8" height="1.6" fill="#c9d3e3" />))}</g>
    case 'inventory':    // box + scanner
      return <g className="prop"><rect x="42" y="50" width="15" height="12" rx="1" fill="#c79a5b" stroke="#9b7440" strokeWidth=".8" />
        <rect x="48.5" y="50" width="2" height="12" fill="#e8d3a8" /><rect x="10" y="56" width="6" height="4" rx="1" fill="#1d2433" />
        <line className="beam" x1="10" y1="58" x2="3" y2="58" stroke="#ff5f7a" strokeWidth="1" /></g>
    case 'facilities':   // wrench
      return <g className="prop wrench"><rect x="47" y="46" width="3" height="16" rx="1.5" fill="#9aa6bb" />
        <path d="M45 44 a4.5 4.5 0 1 1 7 0 l-2 -1 l-1.5 2.5 l-1.5 -2.5 z" fill="#9aa6bb" /></g>
    case 'customer_service': // speech bubble
      return <g className="prop bubble"><path d="M46 40 h14 a3 3 0 0 1 3 3 v6 a3 3 0 0 1 -3 3 h-8 l-4 4 v-4 h-2 a3 3 0 0 1 -3 -3 v-6 a3 3 0 0 1 3 -3 z" fill="#f4f6fb" />
        <circle cx="50" cy="46" r="1" fill="#286dc0" /><circle cx="53.5" cy="46" r="1" fill="#286dc0" /><circle cx="57" cy="46" r="1" fill="#286dc0" /></g>
  }
}

export function Avatar({ role, state = 'idle', size = 84, delay = 0 }: {
  role: Role; state?: AvatarState; size?: number; delay?: number
}) {
  const look = LOOKS[role]
  return (
    <svg className={`av av-${state} role-${role}`} width={size} height={size * 1.4} viewBox="0 0 64 96" role="img"
         aria-label={LABEL[role]} style={{ ['--d' as string]: `${delay}s` }}>
      <ellipse cx="32" cy="93" rx="15" ry="2.6" fill="#000" opacity=".35" className="shadow" />
      <g className="fig">
        <g className="leg leg-l"><rect x="23" y="64" width="8" height="25" rx="3" fill={look.legs} /><rect x="21" y="86" width="11" height="5" rx="2.5" fill={look.shoes} /></g>
        <g className="leg leg-r"><rect x="33" y="64" width="8" height="25" rx="3" fill={look.legs} /><rect x="32" y="86" width="11" height="5" rx="2.5" fill={look.shoes} /></g>
        <rect x="20" y="35" width="24" height="31" rx="8" fill={look.shirt} />
        <Outfit role={role} />
        <g className="arm arm-l"><rect x="13" y="37" width="7" height="22" rx="3.5" fill={look.sleeve} /><circle cx="16.5" cy="59" r="3.6" fill={look.skin} /></g>
        <g className="arm arm-r"><rect x="44" y="37" width="7" height="22" rx="3.5" fill={look.sleeve} /><circle cx="47.5" cy="59" r="3.6" fill={look.skin} /><Prop role={role} /></g>
        <g className="head">
          <rect x="29" y="30" width="6" height="6" fill={look.skin} />
          <circle cx="32" cy="21" r="12" fill={look.skin} />
          <circle cx="28" cy="22" r="1.4" fill="#1d2433" className="eye" /><circle cx="36" cy="22" r="1.4" fill="#1d2433" className="eye" />
          <path className="mouth" d="M28.5 27 Q32 30 35.5 27" fill="none" stroke="#7a3b2b" strokeWidth="1.3" strokeLinecap="round" />
          <Headwear role={role} look={look} />
        </g>
      </g>
    </svg>
  )
}
