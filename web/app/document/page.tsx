import type { Metadata } from "next";
import { DocumentViewer } from "./document-viewer";

type DocumentPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function normalizeDocumentNumber(value: string | string[] | undefined): string {
  const candidate = Array.isArray(value) ? value[0] : value;
  return (candidate ?? "").trim().slice(0, 80);
}

export async function generateMetadata({ searchParams }: DocumentPageProps): Promise<Metadata> {
  const params = await searchParams;
  const documentNumber = normalizeDocumentNumber(params.number);
  const label = documentNumber ? `${documentNumber} · ` : "";
  return {
    title: `${label}Văn bản pháp quy | MediPay AI`,
    description: "Bản HTML đã làm sạch của văn bản pháp quy trong kho tri thức MediPay AI.",
    robots: { index: false, follow: false },
  };
}

export default async function DocumentPage({ searchParams }: DocumentPageProps) {
  const params = await searchParams;
  return <DocumentViewer documentNumber={normalizeDocumentNumber(params.number)} />;
}
