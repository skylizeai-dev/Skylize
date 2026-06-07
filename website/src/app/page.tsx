import {
  Navigation,
  Hero,
  ClientLogos,
  Problem,
  Solution,
  HowItWorks,
  Agents,
  Roi,
  CaseStudies,
  Testimonials,
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
        <ClientLogos />
        <Problem />
        <Solution />
        <HowItWorks />
        <Agents />
        <Roi />
        <CaseStudies />
        <Testimonials />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </>
  );
}
