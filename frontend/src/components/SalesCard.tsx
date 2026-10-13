import { useEffect, useRef } from "react";

export type CardPerson = {
  posicao: number;
  nome: string;
  nome_curto: string;
  novas_anuais: number;
};

type Props = {
  people: CardPerson[];
  total: number;
  equipe: number;
  site: number;
  unitLabel: string;
  scopeAll: boolean;
  from: string; // yyyy-mm-dd
  to: string;
};

const MONTHS = [
  "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
  "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
];

const RED = "#a50019";
const SERIF = "Georgia, 'Times New Roman', serif";
const SANS = "Arial, Helvetica, sans-serif";
const ACCENTS = ["#c9a227", "#b8bcc4", "#b07d4f", "#808080", "#808080"];
const POS_COLORS = ["#c9a227", "#b8bcc4", "#b07d4f", "#7d7d7d", "#7d7d7d"];

const parts = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m, d };
};
const pad = (n: number) => String(n).padStart(2, "0");
const brFull = (iso: string) => {
  const { y, m, d } = parts(iso);
  return `${pad(d)}/${pad(m)}/${y}`;
};

function localToday() {
  const t = new Date();
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
}

// Textos do cabeçalho, no mesmo estilo do card que a supervisora monta.
export function cardTexts(from: string, to: string) {
  const a = parts(from);
  const b = parts(to);
  const sameMonth = a.y === b.y && a.m === b.m;
  const lastDay = new Date(b.y, b.m, 0).getDate();
  let chip: string;
  if (sameMonth && a.d === 1 && to === localToday()) {
    chip = `${MONTHS[b.m - 1]} ${b.y} · ATÉ O DIA ${b.d}`;
  } else if (sameMonth && a.d === 1 && b.d === lastDay) {
    chip = `FECHAMENTO DE ${MONTHS[b.m - 1]} ${b.y}`;
  } else {
    chip = `${pad(a.d)}/${pad(a.m)} A ${brFull(to)}`;
  }
  const range = sameMonth ? `${pad(a.d)} a ${brFull(to)}` : `${brFull(from)} a ${brFull(to)}`;
  return { chip, range };
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function spaced(ctx: CanvasRenderingContext2D, value: string) {
  (ctx as unknown as { letterSpacing: string }).letterSpacing = value;
}

function draw(canvas: HTMLCanvasElement, p: Props) {
  const W = 1080;
  const rows = p.people.slice(0, 5);
  const rest = p.people.slice(5, 10);
  const rowH = 139;
  const gap = 19;
  const rowsTop = 460;
  const rowsBottom = rowsTop + Math.max(rows.length, 1) * (rowH + gap) - gap;
  const boxTop = rowsBottom + (rest.length ? 109 : 56);
  const boxH = 119;
  const footBase = boxTop + boxH + 50;
  const H = footBase + 40;

  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { chip, range } = cardTexts(p.from, p.to);

  ctx.fillStyle = "#111111";
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = RED;
  ctx.fillRect(0, 0, W, 14);
  ctx.fillRect(0, H - 14, W, 14);

  // logo
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "#ffffff";
  ctx.font = `bold 62px ${SANS}`;
  ctx.fillText("MOV", 70, 126);
  const logoX = 70 + ctx.measureText("MOV").width + 14;
  const fitW = ctx.measureText("FIT").width + 28;
  ctx.fillStyle = RED;
  ctx.fillRect(logoX, 64, fitW, 80);
  ctx.fillStyle = "#ffffff";
  ctx.fillText("FIT", logoX + 14, 126);

  // etiqueta do período
  ctx.font = `bold 22px ${SANS}`;
  spaced(ctx, "1px");
  const chipW = ctx.measureText(chip).width + 36;
  ctx.strokeStyle = RED;
  ctx.lineWidth = 2.5;
  roundRect(ctx, 1010 - chipW, 78, chipW, 50, 25);
  ctx.stroke();
  ctx.fillStyle = RED;
  ctx.fillText(chip, 1010 - chipW + 18, 111);
  spaced(ctx, "0px");

  // título
  ctx.fillStyle = "#ffffff";
  ctx.font = `bold 78px ${SERIF}`;
  ctx.fillText("NOVAS MATRÍCULAS", 70, 270);
  ctx.fillStyle = "#8f8f8f";
  ctx.font = `30px ${SERIF}`;
  ctx.fillText(`Acumulado de ${range} · ${p.unitLabel}`, 70, 330);
  ctx.fillStyle = RED;
  ctx.font = `bold 22px ${SANS}`;
  spaced(ctx, "0.5px");
  ctx.fillText("SOMENTE NOVAS MATRÍCULAS ANUAIS  ·  RENOVAÇÃO NÃO CONTA", 70, 369);
  spaced(ctx, "0px");
  ctx.fillRect(70, 404, 121, 7);

  // ranking (5 primeiros)
  rows.forEach((person, i) => {
    const y = rowsTop + i * (rowH + gap);
    ctx.fillStyle = "#1b1b1b";
    roundRect(ctx, 70, y, 940, rowH, 14);
    ctx.fill();
    ctx.fillStyle = ACCENTS[i] ?? "#808080";
    roundRect(ctx, 70, y, 12, rowH, 6);
    ctx.fill();

    ctx.fillStyle = POS_COLORS[i] ?? "#7d7d7d";
    ctx.font = `bold 58px ${SERIF}`;
    ctx.fillText(`${person.posicao}º`, 108, y + 88);

    ctx.fillStyle = "#ffffff";
    ctx.font = `bold 40px ${SANS}`;
    ctx.fillText(person.nome_curto.toUpperCase(), 260, y + 86);

    ctx.textAlign = "right";
    ctx.font = `bold 74px ${SERIF}`;
    ctx.fillText(String(person.novas_anuais), 953, y + 82);
    ctx.fillStyle = "#8f8f8f";
    ctx.font = `24px ${SANS}`;
    ctx.fillText("matrículas", 953, y + 120);
    ctx.textAlign = "left";
  });

  // 6º em diante
  if (rest.length) {
    ctx.fillStyle = "#8f8f8f";
    ctx.font = `26px ${SANS}`;
    const line = rest.map((r) => `${r.posicao}º ${r.nome_curto} ${r.novas_anuais}`).join("  ·  ");
    ctx.fillText(line, 70, rowsBottom + 47);
  }

  // total
  ctx.fillStyle = RED;
  roundRect(ctx, 70, boxTop, 940, boxH, 22);
  ctx.fill();
  ctx.textAlign = "center";
  ctx.fillStyle = "#ffffff";
  ctx.font = `bold 62px ${SERIF}`;
  ctx.fillText(String(p.total), W / 2, boxTop + 66);
  ctx.fillStyle = "#f3c6cc";
  ctx.font = `bold 22px ${SANS}`;
  spaced(ctx, "0.5px");
  ctx.fillText(
    `NOVAS MATRÍCULAS ANUAIS ${p.scopeAll ? "NAS UNIDADES" : "NA UNIDADE"}`,
    W / 2,
    boxTop + 100
  );
  spaced(ctx, "0px");
  ctx.textAlign = "left";

  ctx.fillStyle = "#8f8f8f";
  ctx.font = `25px ${SANS}`;
  ctx.fillText(`${p.equipe} fechadas pela equipe  ·  ${p.site} pelo site`, 70, footBase);
}

export default function SalesCard(props: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const key = JSON.stringify(props);

  useEffect(() => {
    if (ref.current) draw(ref.current, props);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const download = () => {
    ref.current?.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ranking-vendas-${props.from}-a-${props.to}.png`;
      a.click();
      URL.revokeObjectURL(url);
    }, "image/png");
  };

  return (
    <div className="space-y-3">
      <canvas ref={ref} className="w-full max-w-[420px] rounded-md border border-white/10" />
      <button
        type="button"
        onClick={download}
        className="rounded-md border border-white/15 px-4 py-2 text-sm text-sand/80 hover:bg-white/5"
      >
        Baixar imagem (PNG)
      </button>
    </div>
  );
}
