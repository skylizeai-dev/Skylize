import { Display, Eyebrow, Section, SectionBody } from "../primitives";

export function Apply() {
  return (
    <Section id="apply" band>
      <SectionBody>
        <Eyebrow index="07">Apply</Eyebrow>
        <div className="mt-[18px]">
          <Display className="max-w-[16ch]">Apply as a Design Partner</Display>
          <p className="mt-[22px] max-w-[52ch] text-[1.06rem] leading-[1.7] text-muted-foreground">
            Reach out directly at{" "}
            <a href="mailto:skylize.ai@gmail.com" className="text-blue underline">
              skylize.ai@gmail.com
            </a>
          </p>
        </div>
      </SectionBody>
    </Section>
  );
}
