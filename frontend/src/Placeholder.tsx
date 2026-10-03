export default function Placeholder({ title, stage }: { title: string; stage: number }) {
  return (
    <section className="page">
      <h1 className="page-title">{title}</h1>
      <div className="panel empty">
        <p>This screen arrives in Stage {stage}.</p>
      </div>
    </section>
  )
}
