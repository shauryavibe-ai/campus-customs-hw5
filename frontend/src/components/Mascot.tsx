// The Campus Customs super-courier, drawn in SVG (no image files).
// mood: idle (bobs), running (agents at work), done (ticket delivered).

export type Mood = 'idle' | 'running' | 'done'

export function Mascot({ mood = 'idle', size = 132 }: { mood?: Mood; size?: number }) {
  return (
    <svg className={`mascot mascot-${mood}`} width={size} height={size} viewBox="0 0 160 160" role="img"
         aria-label="Campus Customs super delivery courier carrying parcels">
      {/* speed lines */}
      <g className="speed" stroke="#6fa8ff" strokeWidth="3" strokeLinecap="round">
        <line x1="4" y1="70" x2="26" y2="70" /><line x1="0" y1="86" x2="20" y2="86" /><line x1="8" y1="102" x2="26" y2="102" />
      </g>
      {/* cape */}
      <path className="cape" d="M58 62 C30 78 26 112 34 134 C46 124 56 126 66 132 C64 108 70 86 78 66 Z" fill="#00356b" stroke="#286dc0" strokeWidth="2" />
      <path d="M58 62 C42 76 38 100 42 120" stroke="#6fa8ff" strokeWidth="2" fill="none" opacity=".7" />
      <g className="body">
        {/* legs */}
        <g className="leg leg-back"><rect x="70" y="112" width="11" height="28" rx="5" fill="#14213a" /><rect x="66" y="136" width="18" height="8" rx="4" fill="#286dc0" /></g>
        <g className="leg leg-front"><rect x="84" y="112" width="11" height="28" rx="5" fill="#1a263b" /><rect x="84" y="136" width="18" height="8" rx="4" fill="#6fa8ff" /></g>
        {/* torso */}
        <rect x="64" y="64" width="38" height="54" rx="14" fill="#0f1a2e" stroke="#286dc0" strokeWidth="2" />
        <rect x="64" y="100" width="38" height="7" fill="#286dc0" />
        <circle cx="83" cy="84" r="9" fill="#f4f6fb" />
        <text x="83" y="88" textAnchor="middle" fontSize="9" fontWeight="900" fill="#00356b" fontFamily="system-ui">CC</text>
        {/* head */}
        <circle cx="83" cy="46" r="17" fill="#f1c7a6" />
        <circle cx="89" cy="46" r="2.3" fill="#14213a" />
        <path d="M84 54 Q89 58 94 53" stroke="#14213a" strokeWidth="2" fill="none" strokeLinecap="round" />
        {/* cap with a Y */}
        <path d="M65 42 C66 26 100 24 101 40 Z" fill="#00356b" />
        <rect x="88" y="37" width="22" height="6" rx="3" fill="#286dc0" />
        <text x="80" y="38" textAnchor="middle" fontSize="11" fontWeight="900" fill="#ffffff" fontFamily="Georgia, serif">Y</text>
        {/* arm holding the parcels */}
        <path d="M98 74 Q116 80 116 92" stroke="#0f1a2e" strokeWidth="10" strokeLinecap="round" fill="none" />
        {/* parcels: tee, hoodie, mug */}
        <g className="parcels">
          <rect x="104" y="88" width="40" height="22" rx="3" fill="#1a263b" stroke="#286dc0" strokeWidth="2" />
          <rect x="122" y="88" width="4" height="22" fill="#6fa8ff" opacity=".7" />
          <text x="113" y="103" textAnchor="middle" fontSize="7" fontWeight="800" fill="#a8c8ff" fontFamily="system-ui">TEE</text>
          <rect x="108" y="68" width="34" height="20" rx="3" fill="#14213a" stroke="#6fa8ff" strokeWidth="2" />
          <text x="125" y="82" textAnchor="middle" fontSize="7" fontWeight="800" fill="#a8c8ff" fontFamily="system-ui">HOODIE</text>
          <rect x="114" y="52" width="22" height="16" rx="3" fill="#1a263b" stroke="#286dc0" strokeWidth="2" />
          <text x="125" y="63" textAnchor="middle" fontSize="6.5" fontWeight="800" fill="#a8c8ff" fontFamily="system-ui">MUG</text>
          <rect x="131" y="54" width="5" height="5" rx="1" fill="#ff6fb1" />{/* tiny "fragile" sticker: the one pink accent */}
        </g>
        <circle cx="112" cy="94" r="5" fill="#f1c7a6" />
      </g>
      {/* delivered sparkle */}
      <g className="spark" fill="#a8c8ff">
        <path d="M140 20 l3 8 8 3 -8 3 -3 8 -3 -8 -8 -3 8 -3 z" />
        <path d="M118 8 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2 z" />
      </g>
    </svg>
  )
}
