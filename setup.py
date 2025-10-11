from setuptools import setup, find_packages

setup(
    name="sequencer",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "mido[ports-rtmidi]>=1.2.10",
        "pydub>=0.25.1",
        "simpleaudio>=1.0.4",
    ],
    author="Jules",
    author_email="jules@example.com",
    description="A minimalist audio/MIDI sequencer in Python.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/example/sequencer", # Replace with actual URL
    entry_points={
        'console_scripts': [
            'sequencer=sequencer.main:main',
        ],
    },
)
