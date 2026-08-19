import {
  Navigation,
  Hero,
  Problem,
  Solution,
  HowItWorks,
  Controls,
  Status,
  Faq,
  FinalCta,
  Footer,
} from "@/components/sections";

export default function Home() {
  return (
    <>
      <Navigation />
      <main>
        <Hero />
        <Problem />
        <Solution />
        <HowItWorks />
        <Controls />
        <Status />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </>
  );
}
