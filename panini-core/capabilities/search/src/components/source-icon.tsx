import {
  Bot,
  BrainCircuit,
  BriefcaseBusiness,
  Globe,
  GraduationCap,
  Newspaper,
  Plane,
  Search,
  ShieldCheck,
} from 'lucide-react';
import {
  siArxiv,
  siGithub,
  siImdb,
  siReddit,
  siWechat,
  siWikipedia,
  siX,
  siYcombinator,
  siYoutube,
  type SimpleIcon,
} from 'simple-icons';
import type { SourceId } from '@/lib/sources';
import { cn } from '@/lib/utils';

const ICONS: Partial<Record<SourceId, SimpleIcon>> = {
  hackernews: siYcombinator,
  reddit: siReddit,
  github: siGithub,
  x: siX,
  arxiv: siArxiv,
  youtube: siYoutube,
  wikipedia: siWikipedia,
  imdb: siImdb,
  wechat: siWechat,
};

const LINE_ICONS = {
  yahoo: Search,
  brave: ShieldCheck,
  aianswers: BrainCircuit,
  huggingface: Bot,
  jobs: BriefcaseBusiness,
  linkedin: BriefcaseBusiness,
  naukrigulf: BriefcaseBusiness,
  bayt: BriefcaseBusiness,
  gulftalent: BriefcaseBusiness,
  indeeduae: BriefcaseBusiness,
  wellfound: BriefcaseBusiness,
  talentnetworks: BriefcaseBusiness,
  flights: Plane,
  scholar: GraduationCap,
  editorial: Newspaper,
} as const;

/** Brand colour for the source, as a CSS colour. Black-on-white brands get the text colour so they survive dark mode. */
export function sourceColor(id: SourceId): string {
  if (id === 'web' || id in LINE_ICONS) return '#60a5fa';
  const hex = ICONS[id]?.hex;
  if (!hex) return 'currentColor';
  return hex === '000000' || hex === '181717' ? 'currentColor' : `#${hex}`;
}

export function SourceIcon({
  id,
  on = true,
  className,
}: {
  id: SourceId;
  on?: boolean;
  className?: string;
}) {
  if (id === 'web')
    return (
      <Globe
        aria-hidden
        className={cn('shrink-0', className ?? 'size-3.5')}
        style={{ opacity: on ? 1 : 0.45 }}
      />
    );
  const LineIcon = id in LINE_ICONS ? LINE_ICONS[id as keyof typeof LINE_ICONS] : undefined;
  if (LineIcon)
    return (
      <LineIcon
        aria-hidden
        className={cn('shrink-0', className ?? 'size-3.5')}
        style={{ opacity: on ? 1 : 0.45 }}
      />
    );
  const icon = ICONS[id];
  if (!icon)
    return (
      <Globe
        aria-hidden
        className={cn('shrink-0', className ?? 'size-3.5')}
        style={{ opacity: on ? 1 : 0.45 }}
      />
    );
  return (
    <svg
      aria-hidden
      className={cn('shrink-0 transition-colors duration-200', className ?? 'size-3.5')}
      fill={on ? sourceColor(id) : 'currentColor'}
      style={on ? undefined : { opacity: 0.45 }}
      viewBox="0 0 24 24"
    >
      <path d={icon.path} />
    </svg>
  );
}
