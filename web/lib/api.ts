const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ChatResponse = {
  response: string;
  analysis: string;
};

export async function sendChatMessage(message: string): Promise<ChatResponse> {
  const response = await fetch(`${apiUrl}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  if (!response.ok) {
    throw new Error("Không thể kết nối MediPay Agent");
  }

  return response.json() as Promise<ChatResponse>;
}
