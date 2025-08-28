from setuptools import setup, find_packages

setup(
    name="sequencer",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "midiutil==1.2.1",
    ],
    author="Jules",
    author_email="jules@example.com",
    description="A minimalist audio/MIDI sequencer in Python.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/example/sequencer", # Replace with actual URL
)
