import Shell from "@/components/teacher/Shell";

export default function TeacherLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <Shell>{children}</Shell>;
}
