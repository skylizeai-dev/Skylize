import {
  SiteHeader,
  SiteFooter,
  Hero,
  WhatWeSolve,
  Solution,
  HowItWorks,
  Controls,
  Status,
  Faq,
  Apply,
} from "@/components/site";

export default function Home() {
  return (
    <>
      <SiteHeader current="home" />
      <main>
        <Hero />
        <WhatWeSolve />
        <Solution />
        <HowItWorks />
        <Controls />
        <Status />
        <Faq />
        <Apply />
      </main>
      <SiteFooter />
    </>
  );
}
