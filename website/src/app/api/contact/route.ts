import { NextRequest, NextResponse } from 'next/server'

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
    const { WEB3FORMS_ACCESS_KEY } = process.env
    if (!WEB3FORMS_ACCESS_KEY) {
      console.error('Contact form is not configured: missing WEB3FORMS_ACCESS_KEY')
      return NextResponse.json(
        { error: 'Contact form is not configured. Please try again later.' },
        { status: 503 }
      )
    }

    const web3formsRes = await fetch('https://api.web3forms.com/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        access_key: WEB3FORMS_ACCESS_KEY,
        subject: `New contact form submission from ${name}`,
        name,
        email,
        company: company || '(not provided)',
        message,
      }),
    })

    const web3formsData = await web3formsRes.json()

    if (!web3formsRes.ok || !web3formsData.success) {
      console.error('Web3Forms failed to send contact form submission:', web3formsData)
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
