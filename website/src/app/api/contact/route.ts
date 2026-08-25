import { NextRequest, NextResponse } from 'next/server'
import { Resend } from 'resend'

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const { name, email, company, message } = body

    if (!name || !email || !message) {
      return NextResponse.json(
        { error: 'Name, email, and message are required' },
        { status: 400 }
      )
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (!emailRegex.test(email)) {
      return NextResponse.json(
        { error: 'Invalid email address' },
        { status: 400 }
      )
    }

    // Mirrors the N8N_API_URL pattern elsewhere in this BFF: unset config
    // means the route answers 503 rather than silently dropping the message.
    const { RESEND_API_KEY, CONTACT_TO_EMAIL, CONTACT_FROM_EMAIL } = process.env
    if (!RESEND_API_KEY || !CONTACT_TO_EMAIL || !CONTACT_FROM_EMAIL) {
      console.error('Contact form is not configured: missing RESEND_API_KEY, CONTACT_TO_EMAIL, or CONTACT_FROM_EMAIL')
      return NextResponse.json(
        { error: 'Contact form is not configured. Please try again later.' },
        { status: 503 }
      )
    }

    const resend = new Resend(RESEND_API_KEY)
    const { error } = await resend.emails.send({
      from: CONTACT_FROM_EMAIL,
      to: CONTACT_TO_EMAIL,
      replyTo: email,
      subject: `New contact form submission from ${name}`,
      text: [
        `Name: ${name}`,
        `Email: ${email}`,
        `Company: ${company || '(not provided)'}`,
        '',
        message,
      ].join('\n'),
    })

    if (error) {
      console.error('Resend failed to send contact form submission:', error)
      return NextResponse.json(
        { error: 'Something went wrong. Please try again.' },
        { status: 502 }
      )
    }

    return NextResponse.json(
      { success: true, message: 'Message received. We will be in touch within one business day.' },
      { status: 200 }
    )
  } catch {
    return NextResponse.json(
      { error: 'Something went wrong. Please try again.' },
      { status: 500 }
    )
  }
}
